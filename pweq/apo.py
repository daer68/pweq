# SPDX-License-Identifier: GPL-3.0-or-later
"""Equalizer APO / AutoEQ parametric EQ text format.

    Preamp: -6.8 dB
    Filter 1: ON PK Fc 21 Hz Gain 6.7 dB Q 1.100
    Filter 2: ON LSC Fc 105 Hz Gain 7.6 dB Q 0.70
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# APO filter type -> PipeWire builtin biquad label.
FILTER_TYPES = {
    "PK": "bq_peaking",
    "PEQ": "bq_peaking",
    "LS": "bq_lowshelf",
    "LSC": "bq_lowshelf",
    "HS": "bq_highshelf",
    "HSC": "bq_highshelf",
    "LP": "bq_lowpass",
    "LPQ": "bq_lowpass",
    "HP": "bq_highpass",
    "HPQ": "bq_highpass",
    "BP": "bq_bandpass",
    "NO": "bq_notch",
    "AP": "bq_allpass",
}

# Types whose gain is meaningful; the others ignore it.
GAIN_TYPES = {"bq_peaking", "bq_lowshelf", "bq_highshelf"}

DEFAULT_Q = 0.707

_NUM = r"([-+]?\d+(?:\.\d+)?)"
_PREAMP_RE = re.compile(r"^preamp\s*:\s*" + _NUM + r"\s*(?:db)?$", re.I)
_FILTER_RE = re.compile(r"^filter\s*\d*\s*:\s*(.*)$", re.I)


class ParseError(ValueError):
    def __init__(self, line_no: int, line: str, reason: str):
        super().__init__(f"line {line_no}: {reason}: {line.strip()!r}")
        self.line_no = line_no
        self.reason = reason


@dataclass
class Band:
    type: str  # APO type code, upper case, e.g. "PK"
    freq: float
    gain: float = 0.0
    q: float = DEFAULT_Q
    enabled: bool = True

    @property
    def label(self) -> str:
        return FILTER_TYPES[self.type]

    def describe(self) -> str:
        s = f"{self.type} {_fmt(self.freq)} Hz"
        if self.label in GAIN_TYPES:
            s += f"  {self.gain:+.1f} dB"
        s += f"  Q {_fmt(self.q)}"
        return s if self.enabled else s + "  (off)"


@dataclass
class Preset:
    preamp: float = 0.0
    bands: list[Band] = field(default_factory=list)

    @property
    def active_bands(self) -> list[Band]:
        return [b for b in self.bands if b.enabled]

    def summary(self) -> str:
        n = len(self.active_bands)
        return f"Preamp {self.preamp:+.1f} dB · {n} band{'' if n == 1 else 's'}"


def parse(text: str) -> Preset:
    preset = Preset()
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if m := _PREAMP_RE.match(line):
            preset.preamp += float(m.group(1))
        elif m := _FILTER_RE.match(line):
            preset.bands.append(_parse_filter(m.group(1), line_no, raw))
        else:
            raise ParseError(line_no, raw, "expected 'Preamp:' or 'Filter N:'")
    return preset


def _parse_filter(body: str, line_no: int, raw: str) -> Band:
    tokens = body.split()
    if len(tokens) < 2 or tokens[0].upper() not in ("ON", "OFF"):
        raise ParseError(line_no, raw, "filter must start with ON or OFF")
    enabled = tokens[0].upper() == "ON"
    ftype = tokens[1].upper()
    if ftype not in FILTER_TYPES:
        known = ", ".join(sorted(FILTER_TYPES))
        raise ParseError(line_no, raw, f"unsupported filter type {tokens[1]!r} (supported: {known})")

    rest = " ".join(tokens[2:])
    freq = _field(rest, r"\bfc\s+" + _NUM)
    if freq is None:
        raise ParseError(line_no, raw, "missing 'Fc <freq> Hz'")
    if not 1 <= freq <= 96000:
        raise ParseError(line_no, raw, f"frequency {_fmt(freq)} Hz out of range")
    gain = _field(rest, r"\bgain\s+" + _NUM)
    q = _field(rest, r"\bq\s+" + _NUM)
    if q is not None and q <= 0:
        raise ParseError(line_no, raw, "Q must be positive")
    if FILTER_TYPES[ftype] in GAIN_TYPES and gain is None:
        raise ParseError(line_no, raw, "missing 'Gain <n> dB'")
    return Band(ftype, freq, gain or 0.0, DEFAULT_Q if q is None else q, enabled)


def _field(text: str, pattern: str) -> float | None:
    m = re.search(pattern, text, re.I)
    return float(m.group(1)) if m else None


def dumps(preset: Preset) -> str:
    lines = [f"Preamp: {preset.preamp:.2f} dB"]
    for i, b in enumerate(preset.bands, 1):
        state = "ON" if b.enabled else "OFF"
        lines.append(f"Filter {i}: {state} {b.type} Fc {_fmt(b.freq)} Hz Gain {b.gain:.2f} dB Q {b.q:.3f}")
    return "\n".join(lines) + "\n"


def _fmt(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")
