# SPDX-License-Identifier: GPL-3.0-or-later
"""Presets (APO .txt files) and per-output preset assignments."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from . import apo


def config_dir() -> Path:
    if override := os.environ.get("PWEQ_CONFIG_DIR"):
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "pweq"


def presets_dir() -> Path:
    return config_dir() / "presets"


def assignments_path() -> Path:
    return config_dir() / "assignments.json"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / "pweq"


# --- presets ---------------------------------------------------------------


def list_presets() -> dict[str, Path]:
    d = presets_dir()
    if not d.is_dir():
        return {}
    return {p.stem: p for p in sorted(d.glob("*.txt"), key=lambda p: p.stem.lower())}


def preset_path(name: str) -> Path:
    return presets_dir() / f"{name}.txt"


def load_preset(name: str) -> apo.Preset:
    """Raises FileNotFoundError or apo.ParseError."""
    return apo.parse(preset_path(name).read_text())


def import_preset(src: Path, name: str | None = None) -> str:
    """Validate an APO file and copy it into the presets dir. Returns the preset name."""
    text = Path(src).read_text()
    apo.parse(text)
    name = name or Path(src).stem
    presets_dir().mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, preset_path(name))
    return name


def save_preset(name: str, preset: apo.Preset) -> None:
    presets_dir().mkdir(parents=True, exist_ok=True)
    _write_atomic(preset_path(name), apo.dumps(preset))


def delete_preset(name: str) -> None:
    preset_path(name).unlink(missing_ok=True)


# --- assignments -----------------------------------------------------------


@dataclass
class Assignment:
    name: str  # PipeWire node.name of the output when it was assigned
    description: str  # node.description; fallback match when node.name changes
    preset: str


def load_assignments() -> list[Assignment]:
    try:
        data = json.loads(assignments_path().read_text())
    except FileNotFoundError:
        return []
    return [Assignment(**o) for o in data.get("outputs", [])]


def save_assignments(items: list[Assignment]) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    data = {"outputs": [asdict(a) for a in items]}
    _write_atomic(assignments_path(), json.dumps(data, indent=2) + "\n")


def set_assignment(items: list[Assignment], name: str, description: str, preset: str | None) -> list[Assignment]:
    """Return a new list with the output's assignment replaced (preset None removes it)."""
    out = [a for a in items if a.name != name]
    if preset:
        out.append(Assignment(name, description, preset))
    return out


def match(items: list[Assignment], name: str, description: str) -> Assignment | None:
    """Find the assignment for a live output: exact node.name first, then a unique description.

    The description fallback keeps AirPlay speakers working after their IP
    (which is part of the node.name) changes.
    """
    for a in items:
        if a.name == name:
            return a
    by_desc = [a for a in items if a.description and a.description == description]
    return by_desc[0] if len(by_desc) == 1 else None


# --- runtime state (cleared at logout/reboot) --------------------------------


def state_path() -> Path:
    return runtime_dir() / "state.json"


def load_bypassed() -> set[str]:
    """Keys of outputs the user selected without EQ (see daemon.update_bypass)."""
    try:
        return set(json.loads(state_path().read_text()).get("bypassed", []))
    except (FileNotFoundError, ValueError):
        return set()


def save_bypassed(keys: set[str]) -> None:
    runtime_dir().mkdir(parents=True, exist_ok=True)
    _write_atomic(state_path(), json.dumps({"bypassed": sorted(keys)}) + "\n")


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
