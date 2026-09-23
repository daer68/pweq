# SPDX-License-Identifier: GPL-3.0-or-later
"""pweq — per-output parametric EQ for PipeWire.

usage:
  pweq                     open the GUI
  pweq status              outputs, assigned presets and active EQ sinks
  pweq import FILE [NAME]  add an APO/AutoEQ .txt preset
  pweq assign OUTPUT PRESET|none
                           OUTPUT is a node.name or description (see status)
  pweq import-easyeffects  import EasyEffects presets and autoload rules
  pweq reload              tell the daemon to re-read presets and assignments
  pweq daemon              run the auto-follow daemon (normally via systemd)
  pweq --version

docs: https://github.com/daer68/pweq
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import __version__, apo, chain, config
from .service import reload_daemon


def cmd_status() -> int:
    from . import pw

    graph = pw.dump()
    assignments = config.load_assignments()
    default = graph.default_sink()
    names = graph.node_names()
    outputs = graph.outputs()
    devices = [o.device for o in outputs]
    bypassed = config.load_bypassed()
    live = set()
    for out in outputs:
        a = config.match(assignments, out.name, out.description)
        live.add(a.name if a else None)
        mark = "*" if default == out.name else " "
        line = f"{mark} {out.label}\n      {out.name}\n      preset: {a.preset if a else 'none'}"
        if a:
            key = chain.slug(a.name)
            sink = chain.sink_name(key)
            if key in bypassed and sink in names:
                state = "bypassed: selected without EQ; select its (EQ: …) output to re-enable"
            else:
                state = "active" if sink in names else "not running"
            line += f"  [{sink}: {state}{', default' if default == sink else ''}]"
        print(line)
    for a in assignments:
        if a.name not in live:
            label = pw.short_label(a.description or a.name, devices)
            print(f"  {label} (not connected)\n      {a.name}\n      preset: {a.preset}")
    return 0


def cmd_assign(output: str, preset: str) -> int:
    from . import pw

    preset = None if preset.lower() == "none" else preset
    if preset and preset not in config.list_presets():
        print(f"no such preset: {preset}", file=sys.stderr)
        return 1
    outputs = pw.dump().outputs()
    assignments = config.load_assignments()
    devices = [o.device for o in outputs]
    # node.name -> description; live outputs win over stored assignments.
    known = {a.name: a.description for a in assignments} | {o.name: o.description for o in outputs}
    wanted = output.casefold()
    hits = [
        (name, desc)
        for name, desc in known.items()
        if wanted in (name.casefold(), desc.casefold(), pw.short_label(desc, devices).casefold())
    ]
    if len(hits) != 1:
        print(f"output {output!r} {'is ambiguous' if hits else 'not found'}; see `pweq status`", file=sys.stderr)
        return 1
    name, desc = hits[0]
    config.save_assignments(config.set_assignment(assignments, name, desc, preset))
    return _reloaded()


def _reloaded() -> int:
    if not reload_daemon():
        print("saved; daemon not running (systemctl --user enable --now pweq)", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else "gui"
    if cmd in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if cmd in ("-V", "--version"):
        print(f"pweq {__version__}")
        return 0
    try:
        if cmd == "gui" and len(args) <= 1:
            from . import gui

            return gui.main()
        if cmd == "daemon" and len(args) == 1:
            from . import daemon

            return daemon.main()
        if cmd == "status" and len(args) == 1:
            return cmd_status()
        if cmd == "import" and len(args) in (2, 3):
            name = config.import_preset(Path(args[1]), args[2] if len(args) == 3 else None)
            print(f"imported {name}: {config.load_preset(name).summary()}", flush=True)
            return _reloaded()
        if cmd == "assign" and len(args) == 3:
            return cmd_assign(args[1], args[2])
        if cmd == "import-easyeffects" and len(args) == 1:
            from . import easyeffects

            print("\n".join(easyeffects.import_all()), flush=True)
            return _reloaded()
        if cmd == "reload" and len(args) == 1:
            return 0 if reload_daemon() else 1
    except (OSError, apo.ParseError) as e:
        print(f"pweq: {e}", file=sys.stderr)
        return 1
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
