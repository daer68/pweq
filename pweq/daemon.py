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
from dataclasses import dataclass

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


def redirect_target(default: str | None, running: dict[str, Instance], node_names: set[str]) -> str | None:
    """EQ sink the default should move to, if the default is an output we equalize."""
    for inst in running.values():
        if inst.target == default and inst.sink in node_names:
            return inst.sink
    return None


class Daemon:
    def __init__(self) -> None:
        self.graph = pw.Graph()
        self.procs: dict[str, tuple[Instance, subprocess.Popen]] = {}
        self.failed_at: dict[str, float] = {}
        self.assignments = config.load_assignments()
        self.requested: tuple[str | None, str] | None = None  # last (default, redirect) sent
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
        target = redirect_target(default, running, self.graph.node_names())
        # WirePlumber confirms a default change a few events later; don't repeat it.
        if target and self.requested != (default, target):
            log(f"default {default} -> {target}")
            pw.set_default_sink(target)
        self.requested = (default, target) if target else None
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
