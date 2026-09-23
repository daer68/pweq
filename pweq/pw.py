# SPDX-License-Identifier: GPL-3.0-or-later
"""PipeWire graph state from `pw-dump`, and default-sink control."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass

from .chain import NODE_PREFIX

METADATA_TYPE = "PipeWire:Interface:Metadata"
NODE_TYPE = "PipeWire:Interface:Node"


@dataclass(frozen=True)
class Output:
    id: int
    name: str
    description: str
    device: str = ""  # card description, e.g. "500 Series ... (HD Audio)"

    @property
    def label(self) -> str:
        """Port name without the card prefix, e.g. "Speaker"."""
        return short_label(self.description, [self.device])


def short_label(description: str, devices: list[str]) -> str:
    for dev in devices:
        if dev and description.startswith(dev + " "):
            return description[len(dev) + 1 :]
    return description


class Graph:
    """Objects from pw-dump, kept current by applying monitor batches."""

    def __init__(self) -> None:
        self.objects: dict[int, dict] = {}
        self.metadata: dict[str, dict[tuple[int, str], object]] = {}

    def apply(self, batch: list[dict]) -> None:
        for obj in batch:
            oid = obj.get("id")
            if oid is None:
                continue
            if obj.get("type") == METADATA_TYPE or "metadata" in obj:
                self._apply_metadata(oid, obj)
            elif obj.get("info") is None:
                self._remove(oid)
            else:
                self.objects[oid] = obj

    def _apply_metadata(self, oid: int, obj: dict) -> None:
        prev = self.objects.get(oid, {})
        name = (obj.get("props") or prev.get("props") or {}).get("metadata.name")
        if name is None:
            return
        merged = {**prev, **{k: v for k, v in obj.items() if k != "metadata"}}
        self.objects[oid] = merged
        entries = self.metadata.setdefault(name, {})
        for e in obj.get("metadata") or []:
            k = (e.get("subject", 0), e.get("key"))
            if e.get("value") is None:
                entries.pop(k, None)
            else:
                entries[k] = e["value"]

    def _remove(self, oid: int) -> None:
        obj = self.objects.pop(oid, None)
        if obj and obj.get("type") == METADATA_TYPE:
            self.metadata.pop((obj.get("props") or {}).get("metadata.name"), None)

    def nodes(self) -> Iterator[tuple[int, dict]]:
        for oid, obj in self.objects.items():
            if obj.get("type") == NODE_TYPE:
                yield oid, (obj.get("info") or {}).get("props") or {}

    def outputs(self) -> list[Output]:
        """Real audio outputs: hardware, network (AirPlay) and similar sinks."""
        result = []
        for oid, p in self.nodes():
            if p.get("media.class") != "Audio/Sink":
                continue
            name = p.get("node.name", "")
            if name.startswith(NODE_PREFIX) or p.get("factory.name") == "support.null-audio-sink":
                continue
            if str(p.get("node.virtual", "false")).lower() == "true":
                continue
            device = (self.objects.get(p.get("device.id"), {}).get("info") or {}).get("props") or {}
            result.append(Output(oid, name, p.get("node.description") or name, device.get("device.description", "")))
        return sorted(result, key=lambda o: o.description.lower())

    def node_names(self) -> set[str]:
        return {p.get("node.name", "") for _, p in self.nodes()}

    def default_sink(self) -> str | None:
        value = self.metadata.get("default", {}).get((0, "default.audio.sink"))
        return value.get("name") if isinstance(value, dict) else None


class BatchSplitter:
    """Split `pw-dump --monitor` output into JSON arrays.

    pw-dump pretty-prints every batch as an array whose closing bracket is
    alone on a line at column 0. Fed raw bytes so it works with select().
    """

    def __init__(self) -> None:
        self._pending = b""
        self._lines: list[bytes] = []

    def feed(self, data: bytes) -> list[list[dict]]:
        batches = []
        *complete, self._pending = (self._pending + data).split(b"\n")
        for line in complete:
            self._lines.append(line)
            if line == b"]":
                batches.append(json.loads(b"\n".join(self._lines)))
                self._lines.clear()
        return batches


def dump() -> Graph:
    g = Graph()
    out = subprocess.run(["pw-dump", "--no-colors"], capture_output=True, text=True, check=True).stdout
    g.apply(json.loads(out))
    return g


def set_default_sink(node_name: str) -> None:
    subprocess.run(
        [
            "pw-metadata", "-n", "default", "0",
            "default.configured.audio.sink", json.dumps({"name": node_name}), "Spa:String:JSON",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
