"""Presets designed with the rig switched off, kept until it is switched on.

Most of what this needs already existed. The planner never touches the wire:
it emits a plan in a closed action vocabulary, and a plan is data. Validation
runs against the registry, which is local files. The grounding catalogs, all
331 amps and 2,237 cabs, are local files. The only line in the planning path
that needs hardware is the one that reads current state for context.

So a design is a plan that has passed validation and is waiting for a device.

WHAT IT ANCHORS TO
------------------
"Build a Vox AC30 clean" needs no device. "Bump the presence a bit" is
meaningless without knowing what the presence currently is, and guessing would
be the exact sin this project refuses everywhere else. So a design records the
value every action was computed against, taken from the last real read of that
preset.

That anchor is what makes reconnecting a merge rather than a hope. On send,
the values are read again and compared. Three outcomes:

    clean      nothing moved; apply and verify as normal
    drifted    something moved, but not where this design edits; say so
    conflict   something moved underneath the edits themselves; refuse

The third is why this is worth building properly. A queue that applies blindly
would happily overwrite an edit made on the front panel in between, and the
owner would have no way to know it had.

WHAT IT NEVER CLAIMS
--------------------
A design is not verified. There is nothing to read back from, so it carries
no green tick and says "designed, not yet on hardware" until it lands. Ears
still outrank all of it.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

#: Designs are the owner's own work, not paid content, so unlike the tone
#: library they are safe to keep in the repo tree. Gitignored anyway: whether
#: to publish one is a decision, not a default.
def designs_dir() -> Path:
    override = os.environ.get("TONECOMMAND_DESIGNS_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "designs"


def _path(design_id: str) -> Path:
    # uuid4 hex only, so a crafted id cannot walk out of the directory
    if not design_id or not all(c in "0123456789abcdef" for c in design_id):
        raise ValueError(f"not a design id: {design_id!r}")
    return designs_dir() / f"{design_id}.json"


def anchor_for(actions: list[dict], values: dict) -> dict:
    """The values these actions were computed against.

    Only the parameters the design actually touches. Storing the whole edit
    buffer would be three thousand numbers to detect a change in four, and the
    four are the ones that matter: a preset moving somewhere this design does
    not edit is worth mentioning, but it is not a conflict.
    """
    out = {}
    for a in actions:
        key = a.get("param")
        if a.get("kind") == "set_param" and key and key in values:
            out[key] = values[key]
    return out


def save(record: dict) -> dict:
    """Store a validated design. Refuses one that has not passed validation."""
    blocked = [a for a in record.get("actions", [])
               if a.get("validation_errors")]
    if blocked:
        raise ValueError(
            f"{len(blocked)} action(s) failed validation; a design is only "
            f"saved once it would actually run")
    if not record.get("actions"):
        raise ValueError("a design with no actions is not a design")
    d = dict(record)
    d["id"] = uuid.uuid4().hex
    d["created"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    d.setdefault("name", "untitled")
    designs_dir().mkdir(parents=True, exist_ok=True)
    _path(d["id"]).write_text(json.dumps(d, indent=1))
    return d


def load(design_id: str) -> dict | None:
    p = _path(design_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def listing() -> list[dict]:
    """Newest first. A corrupt file is skipped rather than breaking the page."""
    out = []
    d = designs_dir()
    if not d.exists():
        return out
    for f in d.glob("*.json"):
        try:
            out.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, ValueError, OSError):
            continue
    return sorted(out, key=lambda r: r.get("created", ""), reverse=True)


def delete(design_id: str) -> bool:
    p = _path(design_id)
    if not p.exists():
        return False
    p.unlink()
    return True


def check(design: dict, live_preset: int | None, live_values: dict) -> dict:
    """Has the rig moved since this was designed?

    Returns the verdict and the specifics, so the owner is told what changed
    rather than merely that something did.
    """
    want = (design.get("preset") or {}).get("number")
    if want is not None and live_preset is not None and want != live_preset:
        return {"verdict": "wrong_preset",
                "detail": f"designed for preset {want}, the unit is on "
                          f"{live_preset}"}
    anchor = design.get("anchor") or {}
    moved = []
    for key, was in anchor.items():
        now = live_values.get(key)
        if now is None:
            continue
        if isinstance(was, (int, float)) and isinstance(now, (int, float)):
            if abs(now - was) > 0.011:      # display values are rounded to 2dp
                moved.append({"param": key, "was": was, "now": now})
        elif now != was:
            moved.append({"param": key, "was": was, "now": now})
    if moved:
        return {"verdict": "conflict", "moved": moved,
                "detail": f"{len(moved)} value(s) this design edits have "
                          f"changed on the unit since it was designed"}
    return {"verdict": "clean", "moved": []}


def to_recipe(design: dict) -> dict:
    """The shareable form. See docs/RECIPES.md.

    A recipe is HOW to build a tone, never the tone file: it names blocks and
    models by their grounded names and is validated against the receiver's own
    device before a byte is sent. Nothing paid is redistributed, and the anchor
    is dropped, because it describes the author's rig rather than the tone.
    """
    # The same slug rule the filename uses, and the same one the service
    # validates against. Only replacing spaces left punctuation in: a design
    # called "Steve Lukather: Dumble ODS lead" produced the name
    # "steve-lukather:-dumble-ods-lead", which the worker rejects outright and
    # which cannot be a filename either. One rule, in one place.
    from fm9.recipes import _safe_name
    return {
        "recipe_version": 1,
        "name": _safe_name(design.get("name") or design.get("summary")),
        "title": design.get("name") or "untitled",
        "device": "FM9",
        "author": design.get("author") or "",
        "summary": design.get("summary") or "",
        "steps": [
            {k: v for k, v in a.items()
             if k in ("kind", "block", "instance", "param", "value",
                      "bypassed", "type_name", "position", "bank", "reason")}
            for a in design.get("actions", [])
        ],
    }
