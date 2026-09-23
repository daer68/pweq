# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto-follow daemon.

Watches the PipeWire graph and, for every connected output that has a preset
assigned, keeps one `pipewire -c <eq.conf>` process running. When the default
output becomes such a device, the default is moved to its EQ sink so audio
passes through the EQ. Idle between PipeWire events: no polling.
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Collection
from dataclasses import dataclass, field

from . import apo, chain, config, pw

RESTART_BACKOFF = 10.0  # seconds before restarting an EQ process that died


@dataclass(frozen=True)
class Instance:
    key: str
    target: str  # node.name of the real output
    conf: str

    @property
    def sink(self) -> str:
        return chain.sink_name(self.key)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def plan(outputs: list[pw.Output], assignments: list[config.Assignment], load_preset) -> dict[str, Instance]:
    """Desired EQ instances for the currently connected outputs."""
    desired: dict[str, Instance] = {}
    for out in outputs:
        a = config.match(assignments, out.name, out.description)
        if a is None:
            continue
        try:
            preset = load_preset(a.preset)
        except (OSError, apo.ParseError) as e:
            log(f"preset {a.preset!r} for {out.description!r} skipped: {e}")
            continue
        key = chain.slug(a.name)
        desired[key] = Instance(key, out.name, chain.render(key, out.name, out.label, a.preset, preset))
    return desired


@dataclass(frozen=True)
class Snapshot:
    """What the previous reconcile saw."""

    default: str | None = None
    # object.serial of the node the default named when it was set. The default
    # is stored by name, so after a node restarts under the same name this still
    # identifies the old node.
    default_serial: int | None = None
    serials: dict[str, int] = field(default_factory=dict)  # node.name -> object.serial


def default_serial(prev: Snapshot, default: str | None, serials: dict[str, int]) -> int | None:
    """Serial of the node the current default was set to (see Snapshot.default_serial)."""
    if default != prev.default or prev.default_serial is None:
        return serials.get(default)
    return prev.default_serial


def update_bypass(
    bypassed: set[str],
    running: dict[str, Instance],
    default: str | None,
    serials: dict[str, int],
    prev: Snapshot,
    retired: Collection[int] = (),
) -> set[str]:
    """Track outputs the user deliberately selected without EQ.

    Switching the default to a real output counts as "EQ off" only when nothing
    else explains the switch:
    - the output and its EQ sink both existed before, the EQ sink as the same
      node (serials are never reused, unlike ids), so it isn't a plug-in or a
      restarted EQ;
    - the EQ node isn't one we just stopped (`retired`): WirePlumber may switch
      before the removal is reported;
    - the node the previous default pointed to still exists, so it isn't
      WirePlumber falling back after a device or EQ vanished (it may do that
      late, after the restarted EQ is already back under the same name).
    Selecting the EQ sink turns the EQ back on; disconnected outputs are forgotten.
    """
    result = bypassed & running.keys()
    live = set(serials.values())
    for key, inst in running.items():
        if default == inst.sink:
            result.discard(key)
        elif (
            default == inst.target
            and default != prev.default
            and inst.target in prev.serials
            and inst.sink in prev.serials
            and serials.get(inst.sink) == prev.serials[inst.sink]
            and prev.serials[inst.sink] not in retired
            and prev.default_serial in live
        ):
            result.add(key)
    return result


def redirect_target(
    default: str | None,
    running: dict[str, Instance],
    node_names: Collection[str],
    bypassed: Collection[str] = (),
) -> str | None:
    """EQ sink the default should move to, if the default is an output we equalize (and not bypassed)."""
    for key, inst in running.items():
        if inst.target == default and inst.sink in node_names and key not in bypassed:
            return inst.sink
    return None


class Daemon:
    def __init__(self) -> None:
        self.graph = pw.Graph()
        self.procs: dict[str, tuple[Instance, subprocess.Popen]] = {}
        self.failed_at: dict[str, float] = {}
        self.assignments = config.load_assignments()
        self.requested: tuple[str | None, str] | None = None  # last (default, redirect) sent
        self.bypassed = config.load_bypassed()  # keys of outputs selected without EQ
        self.prev = Snapshot()
        self.retired: set[int] = set()  # serials of EQ sinks we stopped, until they leave the graph
        self.reload = False
        self.stop = False

    # --- EQ processes --------------------------------------------------------

    def _start(self, inst: Instance) -> None:
        d = config.runtime_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{inst.key}.conf"
        path.write_text(inst.conf)
        proc = subprocess.Popen(["pipewire", "-c", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        self.procs[inst.key] = (inst, proc)
        log(f"started EQ {inst.sink} -> {inst.target} (pid {proc.pid})")

    def _stop(self, key: str) -> None:
        inst, proc = self.procs.pop(key)
        if (serial := self.graph.node_serials().get(inst.sink)) is not None:
            self.retired.add(serial)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        (config.runtime_dir() / f"{key}.conf").unlink(missing_ok=True)
        log(f"stopped EQ {inst.sink}")

    def _reap(self) -> None:
        for key, (inst, proc) in list(self.procs.items()):
            if proc.poll() is not None:
                log(f"EQ {inst.sink} exited with {proc.returncode}")
                del self.procs[key]
                self.failed_at[key] = time.monotonic()

    def reconcile(self) -> float | None:
        """Bring processes and the default sink in line with the graph.

        Returns seconds until a backed-off restart is due, or None.
        """
        self._reap()
        desired = plan(self.graph.outputs(), self.assignments, config.load_preset)
        for key, (inst, _) in list(self.procs.items()):
            if desired.get(key) != inst:
                self._stop(key)
        waits = []
        now = time.monotonic()
        for key, inst in desired.items():
            if key in self.procs:
                continue
            due = self.failed_at.get(key, -RESTART_BACKOFF) + RESTART_BACKOFF
            if now < due:
                waits.append(due - now)
                continue
            self.failed_at.pop(key, None)
            self._start(inst)

        running = {k: inst for k, (inst, _) in self.procs.items()}
        default = self.graph.default_sink()
        serials = self.graph.node_serials()
        self.retired &= set(serials.values())
        bypassed = update_bypass(self.bypassed, running, default, serials, self.prev, self.retired)
        if bypassed != self.bypassed:
            for key in bypassed - self.bypassed:
                log(f"EQ bypassed: {running[key].target} selected without EQ")
            for key in self.bypassed - bypassed:
                log(f"EQ bypass cleared for {key}")
            self.bypassed = bypassed
            config.save_bypassed(bypassed)

        target = redirect_target(default, running, serials, self.bypassed)
        # WirePlumber confirms a default change a few events later; don't repeat it.
        if target and self.requested != (default, target):
            log(f"default {default} -> {target}")
            pw.set_default_sink(target)
        self.requested = (default, target) if target else None
        self.prev = Snapshot(default, default_serial(self.prev, default, serials), serials)
        return min(waits, default=None)

    def shutdown(self) -> None:
        default = self.graph.default_sink()
        for inst, _ in self.procs.values():
            if inst.sink == default:
                pw.set_default_sink(inst.target)
        for key in list(self.procs):
            self._stop(key)

    # --- main loop -----------------------------------------------------------

    def run(self) -> int:
        rfd, wfd = os.pipe()
        os.set_blocking(wfd, False)
        signal.set_wakeup_fd(wfd)

        def on_signal(signum, _frame):
            if signum == signal.SIGHUP:
                self.reload = True
            elif signum in (signal.SIGTERM, signal.SIGINT):
                self.stop = True

        for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGCHLD):
            signal.signal(s, on_signal)

        mon = subprocess.Popen(
            ["pw-dump", "--monitor", "--no-colors"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL
        )
        sel = selectors.DefaultSelector()
        sel.register(mon.stdout, selectors.EVENT_READ, "pw")
        sel.register(rfd, selectors.EVENT_READ, "signal")
        splitter = pw.BatchSplitter()
        ready = False  # first batch is the full graph
        wait = None
        log("pweq daemon running")
        try:
            while not self.stop:
                dirty = False
                for sk, _ in sel.select(wait):
                    if sk.data == "signal":
                        os.read(rfd, 512)
                        dirty = True
                        continue
                    data = os.read(mon.stdout.fileno(), 65536)
                    if not data:
                        log("pw-dump exited; PipeWire restarted?")
                        return 1
                    for batch in splitter.feed(data):
                        self.graph.apply(batch)
                        ready = dirty = True
                if self.reload:
                    # Presets are re-read by every reconcile; only changed EQs restart.
                    self.reload = False
                    self.assignments = config.load_assignments()
                    self.failed_at.clear()
                    log("reloaded configuration")
                if ready and (dirty or wait is not None) and not self.stop:
                    wait = self.reconcile()
        finally:
            self.shutdown()
            mon.terminate()
        return 0


def main() -> int:
    return Daemon().run()
