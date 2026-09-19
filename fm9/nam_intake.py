"""Bring your own captures, the intake half (issue #149, I6).

One .nam file, several, or a folder: each goes through I1 (fm9.nam) and
comes back as a record with one honest line; duplicates are recognised by
sha256 and reported once; captures of one amp form a set, and a set maps
to channels A to D of one block (four or fewer) or to scenes (more), in
gain order when tone_type is on file and in file order otherwise. Nothing
here reaches the unit: installing is I4's (#147).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from . import nam

CHANNELS = ("A", "B", "C", "D")

#: Gain order for a set whose members all carry tone_type: clean first,
#: the crunch family, then hi_gain. Anything else keeps file order.
_GAIN_RANK = {"clean": 0, "crunch": 1, "overdrive": 1, "fuzz": 1, "hi_gain": 2}

SINGLE_LINE = "added to your library, say the word and I'll build with it"


@dataclass
class Item:
    """One input file as it arrived."""
    name: str
    sha256: str
    record: nam.CaptureRecord | None
    error: str | None = None

    @property
    def line(self) -> str:
        if self.record is None:
            return f"{self.name}: {self.error}"
        return f"{self.name}: {self.record.describe()}"

    def as_dict(self) -> dict:
        return {"name": self.name, "sha256": self.sha256, "line": self.line,
                "record": self.record.as_dict() if self.record else None,
                "error": self.error}


@dataclass
class CaptureSet:
    """Captures of one amp (same gear make and model on file)."""
    gear: str
    items: list[Item]
    mapping: list[dict] = field(default_factory=list)

    @property
    def line(self) -> str:
        n = len(self.items)
        if n <= len(CHANNELS):
            span = f"channels A to {CHANNELS[n - 1]}" if n > 1 else "channel A"
            return f"{n} captures of one amp ({self.gear}), mapped to {span}"
        return f"{n} captures of one amp ({self.gear}), mapped to scenes 1 to {n}"

    def as_dict(self) -> dict:
        return {"gear": self.gear, "count": len(self.items),
                "items": [i.sha256 for i in self.items],
                "mapping": self.mapping, "line": self.line}


@dataclass
class Intake:
    items: list[Item]
    duplicates: list[dict]
    sets: list[CaptureSet]
    singles: list[Item]

    @property
    def line(self) -> str:
        """The one-line result for the command bar."""
        parts = []
        good = [i for i in self.items if i.record is not None]
        bad = [i for i in self.items if i.record is None]
        if len(good) == 1 and not self.sets:
            parts.append(f"{good[0].line}. {SINGLE_LINE}")
        else:
            for s in self.sets:
                parts.append(s.line)
            if self.singles and self.sets:
                parts.append(f"{len(self.singles)} on their own, added to your library")
            elif self.singles:
                parts.append(f"{len(self.singles)} captures added to your library, "
                             "say the word and I'll build with one")
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} already in your library, skipped")
        if bad:
            parts.append(f"{len(bad)} not readable: " + "; ".join(i.line for i in bad))
        return "; ".join(parts) if parts else "nothing to take in"

    def as_dict(self) -> dict:
        return {"items": [i.as_dict() for i in self.items],
                "duplicates": self.duplicates,
                "sets": [s.as_dict() for s in self.sets],
                "singles": [i.sha256 for i in self.singles],
                "line": self.line}


def _read_inputs(inputs) -> list[tuple[str, bytes]]:
    """(name, bytes) for every .nam in the inputs: a path (file or folder,
    walked) or a (name, bytes) pair. Non-.nam files are skipped by name."""
    out: list[tuple[str, bytes]] = []
    for item in inputs:
        if isinstance(item, tuple):
            name, data = item
            if str(name).lower().endswith(".nam"):
                out.append((str(name), bytes(data)))
            continue
        p = Path(item)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() == ".nam":
                    out.append((f.name, f.read_bytes()))
        elif p.is_file() and p.suffix.lower() == ".nam":
            out.append((p.name, p.read_bytes()))
    return out


def intake(inputs, known: set[str] | None = None) -> Intake:
    """Read, describe, dedupe, group and map. `known` is the sha256 set the
    library already holds; a match is a duplicate, reported once."""
    known = set(known or ())
    items: list[Item] = []
    duplicates: list[dict] = []
    seen: dict[str, str] = {}
    for name, data in _read_inputs(inputs):
        digest = hashlib.sha256(data).hexdigest()
        if digest in known or digest in seen:
            duplicates.append({"name": name, "sha256": digest,
                               "same_as": seen.get(digest, "your library")})
            continue
        seen[digest] = name
        try:
            rec = nam.read_nam(data)
            items.append(Item(name, digest, rec))
        except nam.NamError as e:
            items.append(Item(name, digest, None, error=str(e)))
    sets, singles = group(items)
    for s in sets:
        s.mapping = mapping(s)
    return Intake(items, duplicates, sets, singles)


def group(items: list[Item]) -> tuple[list[CaptureSet], list[Item]]:
    """Sets by (gear_make, gear_model), both on file; the rest are singles.
    File order is kept within a set."""
    by_gear: dict[str, list[Item]] = {}
    singles: list[Item] = []
    for it in items:
        r = it.record
        if r is None or not (r.gear_make and r.gear_model):
            if r is not None:
                singles.append(it)
            continue
        key = f"{r.gear_make} {r.gear_model}".strip().lower()
        by_gear.setdefault(key, []).append(it)
    sets = []
    for key, members in by_gear.items():
        if len(members) >= 2:
            sets.append(CaptureSet(members[0].record.gear, members))
        else:
            singles.extend(members)
    return sets, singles


def ordered(members: list[Item]) -> list[Item]:
    """Gain order when every member has a tone_type the rank knows, else
    file order, untouched."""
    kinds = [(m.record.tone_type or "").lower() for m in members]
    if all(k in _GAIN_RANK for k in kinds):
        return sorted(members, key=lambda m: (_GAIN_RANK[m.record.tone_type.lower()],
                                              (m.record.name or m.name).lower()))
    return list(members)


def mapping(cs: CaptureSet) -> list[dict]:
    """Channels A to D for four or fewer, scenes 1..n for more."""
    members = ordered(cs.items)
    if len(members) <= len(CHANNELS):
        return [{"sha256": m.sha256, "name": m.name, "to": "channel",
                 "channel": CHANNELS[i]} for i, m in enumerate(members)]
    return [{"sha256": m.sha256, "name": m.name, "to": "scene", "scene": i + 1}
            for i, m in enumerate(members)]
