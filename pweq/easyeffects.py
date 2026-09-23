# SPDX-License-Identifier: GPL-3.0-or-later
"""Import EasyEffects output presets and per-device autoload rules."""

from __future__ import annotations

import json
from pathlib import Path

from . import apo, config

EE_TYPES = {
    "Bell": "PK",
    "Lo-shelf": "LSC",
    "Hi-shelf": "HSC",
    "Lo-pass": "LP",
    "Hi-pass": "HP",
    "Band-pass": "BP",
    "Notch": "NO",
    "Allpass": "AP",
    "All-pass": "AP",
}


def default_dir() -> Path:
    return Path.home() / ".local/share/easyeffects"


def convert(preset_json: dict) -> tuple[apo.Preset | None, list[str]]:
    """EasyEffects preset -> APO preset. None means no active equalizer (flat)."""
    out = preset_json.get("output", preset_json)
    order = out.get("plugins_order") or [k for k in out if k.startswith("equalizer")]
    warnings = [
        f"plugin {p!r} ignored (only the equalizer is imported)" for p in order if not p.startswith("equalizer")
    ]
    eqs = [out[p] for p in order if p.startswith("equalizer") and p in out]
    if not eqs:
        return None, warnings
    if len(eqs) > 1:
        warnings.append("only the first equalizer is imported")
    eq = eqs[0]
    if eq.get("bypass"):
        return None, warnings
    if eq.get("split-channels"):
        warnings.append("split channels: left channel bands used for both")

    preset = apo.Preset(preamp=float(eq.get("input-gain", 0)) + float(eq.get("output-gain", 0)))
    left = eq.get("left", {})
    for i in range(int(eq.get("num-bands", 0))):
        b = left.get(f"band{i}")
        if not b or b.get("mute"):
            continue
        ftype = EE_TYPES.get(b.get("type"))
        if ftype is None:
            warnings.append(f"band {i + 1}: type {b.get('type')!r} not supported, skipped")
            continue
        if b.get("slope", "x1") != "x1":
            warnings.append(f"band {i + 1}: slope {b['slope']} imported as x1")
        freq, gain, q = float(b["frequency"]), float(b.get("gain", 0)), float(b.get("q", apo.DEFAULT_Q))
        preset.bands.append(apo.Band(ftype, freq, gain, q))
    return preset, warnings


def import_all(ee_dir: Path | None = None) -> list[str]:
    """Import presets and autoload rules; returns a human-readable report."""
    ee_dir = ee_dir or default_dir()
    report: list[str] = []
    imported: set[str] = set()
    flat: set[str] = set()
    for f in sorted((ee_dir / "output").glob("*.json")):
        preset, warnings = convert(json.loads(f.read_text()))
        report += [f"{f.stem}: {w}" for w in warnings]
        if preset is None:
            flat.add(f.stem)
            report.append(f"{f.stem}: no active equalizer, treated as 'no EQ'")
            continue
        config.save_preset(f.stem, preset)
        imported.add(f.stem)
        report.append(f"{f.stem}: imported ({preset.summary()})")

    assignments = config.load_assignments()
    for f in sorted((ee_dir / "autoload" / "output").glob("*.json")):
        rule = json.loads(f.read_text())
        device, desc, name = rule.get("device"), rule.get("device-description", ""), rule.get("preset-name")
        if not device or name in flat:
            continue
        if name not in imported:
            report.append(f"{desc or device}: preset {name!r} not found, skipped")
            continue
        assignments = config.set_assignment(assignments, device, desc, name)
        report.append(f"{desc or device}: assigned {name!r}")
    config.save_assignments(assignments)
    return report
