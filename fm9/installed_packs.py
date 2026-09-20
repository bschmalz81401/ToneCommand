"""Installed artist packs as a reference (issue #159, J6).

A gallery install (#155) lands a pack's preset in a whitelisted store slot
and its cabs in user cab slots. Once it is on the unit it is worth more
than a download: it is a real artist's rig, read back from the blocks.
This module keeps the record of every such install so two things can use
it without touching the catalog again:

- compare (#68): "compare my rhythm tone to Devin's" resolves the name to
  the installed pack, and the server reads that preset off the unit for
  the comparison (server._resolve_source, the pack: source);
- the planner's reference (#6): each install yields EVIDENCE records, the
  block choices read from the pack's preset as it landed (amp model and
  its knobs, the cab, engaged drives, delay and reverb presence), tagged
  with the artist, the year and the words 'pack file'. They are facts read
  from a preset, never a quote, and the reference text says so.

The record lives in one JSON file (TONECOMMAND_INSTALLED_PACKS, else
~/.tonecommand/installed_packs.json). Nothing here talks to the unit.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from fm9 import advisory, artist_pack

VERSION = 1
MAX_PACKS = 200
KINDS = ("amp", "cab", "drive", "delay", "reverb")
SOURCE = "pack file"
_ID = re.compile(r"^[a-z0-9-]{1,64}$")

#: The amp knobs an evidence record carries, by advisory's DISTORT pids.
AMP_KNOBS = ("gain", "bass", "mid", "treble", "master", "presence")


class InstalledPacksError(ValueError):
    """One line, written for the person at the rig."""


def path() -> Path:
    override = os.environ.get("TONECOMMAND_INSTALLED_PACKS", "").strip()
    return Path(override) if override else Path.home() / ".tonecommand" / "installed_packs.json"


def _read() -> dict:
    p = path()
    if not p.exists():
        return {"version": VERSION, "packs": []}
    try:
        doc = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"version": VERSION, "packs": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("packs"), list):
        return {"version": VERSION, "packs": []}
    return doc


def _write(doc: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".part")
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(p)


def listing() -> list[dict]:
    """Every installed pack, newest first. Read-only."""
    packs = list(_read()["packs"])
    packs.sort(key=lambda r: str(r.get("installed_at") or ""), reverse=True)
    return packs


# --- evidence ---------------------------------------------------------------------

def _tag(record: dict) -> dict:
    return {"artist": ", ".join(str(a) for a in (record.get("artists") or [])),
            "year": int(record.get("year") or 0), "source": SOURCE}


def evidence(reg, capture: dict, record: dict) -> list[dict]:
    """Tagged evidence records read from a capture of the pack's preset as
    it landed: the amp model with its knobs, the cab, the engaged drives,
    and whether a delay or a reverb is in the chain. Facts from the
    blocks; no words of the artist's."""
    tag = _tag(record)
    out: list[dict] = []
    for b in capture.get("blocks") or []:
        fam = b.get("family")
        if fam == "DISTORT":
            name = advisory.type_name(reg, b)
            settings = {}
            for knob in AMP_KNOBS:
                v = advisory.value(reg, capture, "DISTORT", advisory.AMP[knob], b.get("instance", 1))
                if v is not None:
                    settings[knob] = v
            out.append({"tag": dict(tag), "kind": "amp", "name": name or "amp",
                        "engaged": not b.get("bypassed"), "settings": settings})
        elif fam == "CABINET":
            out.append({"tag": dict(tag), "kind": "cab", "name": advisory.type_name(reg, b) or "cab",
                        "engaged": not b.get("bypassed"), "settings": {}})
        elif fam == "FUZZ":
            out.append({"tag": dict(tag), "kind": "drive", "name": advisory.type_name(reg, b) or "drive",
                        "engaged": not b.get("bypassed"), "settings": {}})
        elif fam == "DELAY":
            out.append({"tag": dict(tag), "kind": "delay", "name": "delay",
                        "engaged": not b.get("bypassed"), "settings": {}})
        elif fam == "REVERB":
            out.append({"tag": dict(tag), "kind": "reverb", "name": advisory.type_name(reg, b) or "reverb",
                        "engaged": not b.get("bypassed"), "settings": {}})
    return out


def validate_evidence(rec: Any) -> str | None:
    """One line naming what is wrong with an evidence record, or None. A
    record the planner reads must carry its tag whole: no untagged fact
    ever reaches the reference text."""
    if not isinstance(rec, dict):
        return "evidence is not a record"
    tag = rec.get("tag")
    if not isinstance(tag, dict):
        return "evidence has no tag"
    if not str(tag.get("artist") or "").strip():
        return "evidence tag has no artist"
    if not isinstance(tag.get("year"), int) or not 2000 <= tag["year"] <= 2100:
        return "evidence tag has no year"
    if tag.get("source") != SOURCE:
        return f"evidence tag source is not {SOURCE!r}"
    if rec.get("kind") not in KINDS:
        return f"evidence kind {rec.get('kind')!r} is not one of {', '.join(KINDS)}"
    if not str(rec.get("name") or "").strip():
        return "evidence has no name"
    settings = rec.get("settings")
    if not isinstance(settings, dict) or any(
            not isinstance(v, (int, float)) or isinstance(v, bool) for v in settings.values()):
        return "evidence settings must be numbers"
    return None


# --- the record -------------------------------------------------------------------

def record(entry: dict, result: dict, evidence_records: list[dict] | None = None) -> dict:
    """Write the record of one gallery install. `entry` is the catalog
    entry, `result` is gallery_install.execute's dict. A newer install of
    the same entry replaces the older; the file keeps MAX_PACKS."""
    entry_id = str(entry.get("id") or "").strip()
    if not _ID.match(entry_id):
        raise InstalledPacksError(f"pack id {entry_id!r} is not a catalog id")
    ev = []
    for rec in evidence_records or []:
        why = validate_evidence(rec)
        if why:
            raise InstalledPacksError(why)
        ev.append(rec)
    rec = {"id": entry_id,
           "artists": [str(a) for a in (entry.get("artists") or [])],
           "year": int(entry.get("year") or 0),
           "label": ", ".join(str(a) for a in (entry.get("artists") or []))
                    + (f" ({entry.get('year')})" if entry.get("year") else ""),
           "preset_name": str(result.get("preset") or "")[:32],
           "slot": int(result.get("store_slot")),
           "cabs": [{"slot": int(c["slot"]), "name": str(c.get("name") or "")}
                    for c in (result.get("cabs") or [])],
           "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "evidence": ev}
    doc = _read()
    doc["packs"] = [r for r in doc["packs"] if r.get("id") != entry_id] + [rec]
    doc["packs"] = doc["packs"][-MAX_PACKS:]
    doc["version"] = VERSION
    _write(doc)
    return rec


_PHRASE_DROP = frozenset("the a my his her their pack packs rig tone tones sound sounds "
                         "preset presets".split())


def artist_words(phrase: str) -> str:
    """The artist name in a compare object: "Devin's" and "Devin's pack"
    give "devin", "the Periphery rig" gives "periphery". Possessives and
    the pack/rig/tone words go; the artist's own words stay whole."""
    text = str(phrase or "").lower().replace("\u2019", "'")
    text = re.sub(r"(\w)'s\b", r"\1", text)
    text = re.sub(r"(\w)s'\B", r"\1s", text)
    words = [w for w in re.findall(r"[a-z0-9']+", text) if w not in _PHRASE_DROP]
    return " ".join(words)


def find(phrase: str) -> artist_pack.Resolution:
    """Resolve an artist phrase to an installed pack, with artist_pack's
    rules: whole-word match, a full name beats a partial one, one artist
    over several years is the newest, two artists is a question. Nothing
    installed answers 'absent' with one line."""
    packs = listing()
    if not packs:
        return artist_pack.Resolution(
            "absent", phrase, line="no artist pack is installed yet; the Artists "
                                   "shelf installs one in one click")
    res = artist_pack.resolve(phrase, packs)
    if res.status == "absent":
        names = ", ".join(r.get("label") or r.get("id") for r in packs[:6])
        res.line = f"no installed pack matches {phrase!r}; installed: {names}"
    return res


# --- the planner's reference ----------------------------------------------------------

def reference_lines() -> list[str]:
    """The 'Installed artist packs' section for the planner's reference
    text: one line per pack naming the blocks read from its preset, each
    tagged. Empty when nothing is installed."""
    packs = listing()
    if not packs:
        return []
    lines = ["\nINSTALLED ARTIST PACKS (evidence of how a real rig is built: block "
             "choices READ FROM the pack's preset file as installed, tagged with "
             "the artist and year; these are not the artist's words and must "
             "not be quoted as such):"]
    for r in packs:
        bits = []
        for e in r.get("evidence") or []:
            if validate_evidence(e):
                continue
            if e["kind"] == "amp":
                knobs = ", ".join(f"{k} {v:g}" for k, v in (e.get("settings") or {}).items())
                bits.append(f"amp {e['name']}" + (f" ({knobs})" if knobs else ""))
            elif e["kind"] == "cab":
                bits.append(f"cab {e['name']}")
            elif e["kind"] == "drive":
                bits.append(f"drive {e['name']}" + ("" if e.get("engaged") else " (bypassed)"))
            elif e["kind"] in ("delay", "reverb"):
                bits.append(f"{e['kind']} {'on' if e.get('engaged') else 'bypassed'}"
                            + (f" ({e['name']})" if e["kind"] == "reverb" and e.get("name") != "reverb" else ""))
        lines.append(f"- {r.get('label')}: preset {r.get('preset_name')!r} in slot "
                     f"{int(r.get('slot', 0)) + 1}; " + ("; ".join(bits) if bits else "no blocks read")
                     + f" [source: {SOURCE}, {r.get('label')}]")
    return lines
