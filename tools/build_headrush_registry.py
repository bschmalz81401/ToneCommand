#!/usr/bin/env python3
"""Turn the committed HeadRush schema into a block and parameter registry.

    python tools/build_headrush_registry.py
    python tools/build_headrush_registry.py --schema PATH --out PATH --check

#33 phase 3, #122. Hardware is not required and none is touched: the input is
`config/headrush_schema.json`, which is the unit's own self-description as
fetched by tools/build_headrush_schema.py (#117).

WHY A SECOND FILE RATHER THAN READING THE SCHEMA DIRECTLY

The schema is a faithful transcript, organised the way the device's object tree
is organised: 309 objects pointing at 161 deduplicated metas, property meta
carrying JSON Schema keywords beside vendor `x-options`. That shape is right for
a transcript and wrong for a caller, which wants to ask "what are the Amp
block's parameters, what are their ranges, and what does the device call slot
ordinal 1". Every fact here is in the schema already. What this adds is a
classification of each property into the kinds a planner has to treat
differently, and the joins the schema leaves implicit.

WHAT IS DERIVED, AND FROM WHAT

  block roster      /Evil/API/Blocks.ModuleTypes, the 278 entry list that
                    Chain.ModuleType{n} indexes into, committed as an
                    allowlisted roster value by #117
  block objects     every /Evil/Engine/Patch/* object
  the join          module name with spaces as underscores IS the object path
                    leaf, for 274 of the 278. The four that do not join are
                    recorded as such rather than dropped, because three of them
                    are a real device limit (see below) and one is ordinal 0.
  parameter kinds   the published `type` plus `x-options`, nothing else
  units             the literal trailing text of the device's own `format`

WHAT IS NOT DERIVED, DELIBERATELY

Per block CATEGORY. The device has the vocabulary (BlockSelectorCategories) and
a method that answers per block (`categoryOfBlock`), but the mapping is not in
the schema, so it is a live read and is absent here rather than guessed.

An A/B reading of properties whose name ends in `2`. On the Amp object Bass and
Bass2 really are the two halves of a doubled amp, and generalising that would be
wrong: Chain.CanDouble12 is slot twelve, and Vocal_Harmony carries On2, On3 and
On4 for harmony voices. A rule that cannot tell those apart would silently
mislabel parameters, so no rule is applied.

Any FM9 equivalence. Both devices have a block with a knob called Bass and the
two are not interchangeable: they differ in taper, in range and in what the
surrounding circuit does with them. tools/build_headrush_amp_models.py sets out
at length why name similarity across these two rosters produces confident
nonsense, and a parameter level cross-map would be the same mistake with more
entries. Nothing here maps to an FM9 id.

THE WIRE IS NORMALISED AND THE TAPER IS NOT PUBLISHED

Measured on a Core at this firmware, 2026-09-15, by writing a value and reading
the unit's own screen:

    Amp.Bass      wire 0.75  ->  screen 75 %      published range 0..100
    Amp.Treble    wire 0.5   ->  screen 50 %      published range 0..100
    Amp.PostGain  wire 0.5   ->  screen 0.0 dB    published range -12..12
    Amp.TremDepth wire 0.0   ->  screen 0 %       published range 0..100

So `minimum`, `maximum` and `format` describe the DISPLAY scale while the wire
takes 0..1. That is also the only reading under which the published defaults are
sane: SltEQHP defaults to 0.0 on a range of 25..1000 Hz and SltEQLP to 1.0 on
1000..16000 Hz, which as display values are below and above their own published
minimum and maximum, and as normalised values are a high pass off and a low pass
wide open.

The curve BETWEEN the two scales is not published, and is not uniformly linear:

    Amp.TremSpeed wire 0.5   ->  screen 5.19 Hz   published range 0.25..20

whose linear midpoint would be 10.125 Hz. So this registry records both scales
and offers no conversion between them. A helper that assumed linear would be
right on every percentage control and quietly wrong on every frequency, time and
tempo one, which is the worst available failure mode: plausible everywhere and
checkable nowhere.

THREE MODULES THE ROSTER OFFERS AND THE DEVICE DOES NOT BACK

`ReValver Amp 2`, `Neural Amp Modeler 2` and `C-Verb 2` have ModuleType ordinals
and no object. @bschmalz81401 states the corresponding rule from the unit: one
Capture and one C-Verb per rig. The absence of a second object is that rule
showing up in the data, so it is carried as a recorded fact with the ordinal
kept, not quietly dropped to make the join come out even.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
SCHEMA = CONFIG / "headrush_schema.json"
OUT = CONFIG / "headrush_registry.json"

SCHEMA_VERSION = 1
BLOCKS = "/Evil/API/Blocks"
PATCH = "/Evil/Engine/Patch/"

#: Objects whose VALUES are the owner's, not the device's. The schema already
#: withholds every value except an allowlist (#117), so nothing personal is in
#: the input; these are excluded from the registry because a planner that
#: offered them as parameters would be offering to rewrite someone's library.
#: Keyed by path, valued by the reason, so the exclusion is reviewable.
EXCLUDED = {
    "/Evil/API/Rigs": "the owner's rig library",
    "/Evil/API/Setlists": "the owner's setlists",
    "/Evil/API/RigSaveDialog": "a save dialog's transient state",
    "/Evil/API/FileAccess": "file transfer state, and a write surface for it",
    "/Evil/API/Tone3000": "an account bound cloud session",
    "/Evil/Cloud/Master": "an account bound cloud session",
    "/Evil/Engine/Patch/Rig": "carries PresetName, the owner's loaded rig",
}

#: Properties that are presentation state rather than parameters: which controls
#: the unit draws for the currently selected model, in which order, under which
#: labels. They are per model LIVE values, so their contents are not in the
#: schema and cannot be. Kept as a named kind rather than dropped, because a
#: caller reading a block's parameters needs to know these exist.
PRESENTATION = frozenset({"order", "labels", "disabled"})

#: A printf conversion, so the rest of the format string is the unit as the
#: DEVICE spells it. Its spelling is inconsistent (dB and db, Hz and hz) and is
#: preserved rather than normalised: tidying it would put this file's opinion
#: where the device's own string belongs.
_CONVERSION = re.compile(r"^%[-+ 0#]*\d*(?:\.\d+)?[a-zA-Z]")


def fail(message: str) -> None:
    sys.exit(f"build_headrush_registry: {message}")


def fingerprint(schema: dict) -> str:
    """A hash of everything the registry is derived from.

    Not of the whole schema file: `notes` is prose and rewording it should not
    read as device drift. Canonical JSON so the hash is stable across runs.
    """
    material = {k: schema[k] for k in ("firmware", "paths", "metas", "rosters")}
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def unit_of(fmt: str | None) -> str | None:
    """The device's own unit, read off its own format string.

    Refuses rather than guesses. A format this cannot parse means the firmware
    spells them a way that was not measured, and inventing a unit for a
    frequency control is exactly the class of error this repo does not make.
    """
    if fmt is None:
        return None
    rest = _CONVERSION.sub("", fmt, count=1)
    if rest == fmt:
        fail(f"format {fmt!r} has no printf conversion to strip; "
             f"the firmware spells formats differently than measured")
    rest = rest.replace("%%", "%").strip()
    return rest or None


def classify(name: str, meta: dict) -> dict:
    """One property, as the kind a caller has to treat differently.

    The kinds are not a taxonomy invented here. They are the distinctions that
    change what a caller may DO: a continuous value has a range and a taper, a
    selector has a fixed set of named positions, an ordinal has positions with
    no published names, a switch has two, text is free, presentation is not a
    parameter at all.
    """
    if name in PRESENTATION:
        return {"kind": "presentation",
                "note": "live, per selected model; contents are not in the schema"}

    kind = meta.get("type")
    options = (meta.get("x-options") or {})
    strings = options.get("strings")
    default = options.get("default")

    if kind == "number":
        fmt = options.get("format")
        return {
            "kind": "continuous",
            "display_minimum": meta.get("minimum"),
            "display_maximum": meta.get("maximum"),
            "display_format": fmt,
            "unit": unit_of(fmt),
            "default_normalised": default,
        }
    if kind == "integer":
        if strings:
            return {"kind": "selector",
                    "minimum": meta.get("minimum"),
                    "maximum": meta.get("maximum"),
                    "options": list(strings)}
        return {"kind": "ordinal",
                "minimum": meta.get("minimum"),
                "maximum": meta.get("maximum"),
                "options": None,
                "note": "positions are unnamed on this firmware"}
    if kind == "boolean":
        return {"kind": "switch",
                "labels": list(strings) if strings else None,
                "default": default}
    if kind == "string":
        return {"kind": "text", "default": default}
    if kind == "array":
        return {"kind": "list",
                "items": (meta.get("items") or {}).get("type")}
    fail(f"property {name!r} has unhandled type {kind!r}")


def build(schema: dict) -> dict:
    if schema.get("device") != "headrush":
        fail(f"schema is for device {schema.get('device')!r}, not headrush")
    if schema.get("schema_version") != 1:
        fail(f"schema_version {schema.get('schema_version')!r} is not the 1 "
             f"this generator was written against; re-read it before trusting "
             f"a registry built from it")

    paths, metas = schema["paths"], schema["metas"]
    try:
        roster = schema["rosters"][BLOCKS]["ModuleTypes"]
    except KeyError:
        fail(f"{BLOCKS}.ModuleTypes is not in the schema's rosters; without it "
             f"there is nothing for Chain.ModuleType{{n}} to index into")

    # The join, both directions. Name with spaces as underscores is the path
    # leaf; anything that does not join is recorded rather than dropped.
    ordinal_of = {}
    unbacked = []
    for ordinal, name in enumerate(roster):
        path = PATCH + name.replace(" ", "_")
        if path in paths:
            ordinal_of[path] = ordinal
        else:
            unbacked.append({"ordinal": ordinal, "name": name, "would_be": path})

    # Objects share parameter sets exactly as they share metas upstream: 302
    # objects reduce to a fraction of that many distinct sets. Stored once and
    # referenced, the same shape build_headrush_schema.py uses, so the derived
    # file does not end up larger than the schema it is derived from. The
    # dedup is lossless BY CHECK: a hash that already holds a different set is
    # refused rather than assumed not to happen.
    paramsets: dict[str, dict] = {}
    blocks = {}
    for path in sorted(paths):
        if path in EXCLUDED:
            continue
        props = metas[paths[path]].get("properties") or {}
        params = {n: classify(n, props[n]) for n in sorted(props)}
        digest = hashlib.sha256(
            json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]
        if paramsets.setdefault(digest, params) != params:
            fail(f"parameter-set hash collision on {digest} at {path}; the "
                 f"dedup is not safe and the file must not be written")
        blocks[path] = {
            "name": path.rsplit("/", 1)[1],
            "module_ordinal": ordinal_of.get(path),
            "category": None,
            "parameters": digest,
        }

    unregistered = sorted(p for p in blocks
                          if p.startswith(PATCH) and blocks[p]["module_ordinal"] is None)

    notes = [
        "ordinal 0 is 'Empty Slot' and backs no object because it is the "
        "ABSENCE of a block, not a block. It appears in unbacked_modules "
        "beside the three below; they are not the same kind of thing.",

        "three roster entries have an ordinal and no object: ReValver Amp 2 "
        "(4), Neural Amp Modeler 2 (20) and C-Verb 2 (254). @bschmalz81401 "
        "states the rule from the unit as one Capture and one C-Verb per rig. "
        "The missing second object is that rule in the data. Whether writing "
        "one of these ordinals into a slot is refused, ignored or accepted "
        "with nowhere to address it is NOT established here and is a hardware "
        "question (#126).",

        "the patch objects carrying no ordinal are the ones no slot can "
        "select: %s. Derived, not categorised: they are simply absent from "
        "the ModuleTypes roster." % ", ".join(
            p.rsplit("/", 1)[1] for p in unregistered),

        "blocks map an object path to a parameter-set hash and paramsets "
        "holds each set once, the shape build_headrush_schema.py uses for "
        "metas and for the same reason: %d objects carry %d distinct sets. A "
        "hash that already holds a different set is refused, so the dedup is "
        "lossless by check rather than by assertion. Callers ask by path and "
        "devices/headrush/registry.py resolves the indirection." % (
            len(blocks), len(paramsets)),
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "device": "headrush",
        "firmware": schema["firmware"],
        "source": ("config/headrush_schema.json, itself the unit's own "
                   "self-description over GET /api/v1/subtree/. No hardware is "
                   "read by this generator."),
        "generated_by": "tools/build_headrush_registry.py",
        "keyed_by": ("blocks maps an object path to a parameter-set hash; "
                     "paramsets maps that hash to the set, whose keys are the "
                     "device's own property names, unchanged"),
        "content": ("every addressable object's parameters, classified by the "
                    "kind a caller must treat differently, plus the ModuleType "
                    "ordinal that selects each block into a chain slot"),
        "warning": (
            "CONTINUOUS VALUES ARE NORMALISED 0..1 ON THE WIRE while "
            "display_minimum, display_maximum and display_format describe the "
            "scale the unit SHOWS. The curve between them is not published and "
            "is not uniformly linear (Amp.TremSpeed reads 5.19 Hz at wire 0.5 "
            "on a 0.25..20 range, where linear would be 10.125), so no "
            "conversion is offered and none should be assumed. Measured on a "
            "Core at this firmware; see the generator docstring. Block "
            "categories are absent because the device answers them by method "
            "rather than in the schema, and no FM9 equivalence is recorded "
            "because none is evidenced."
        ),
        "schema_fingerprint": fingerprint(schema),
        "wire_encoding": {
            "continuous": "normalised 0..1",
            "measured_on": "HeadRush Core, firmware 5.1.0.2a63755, 2026-09-15",
            "conversion_to_display": None,
            "conversion_note": (
                "not published by the device and not uniformly linear; "
                "Amp.TremSpeed at wire 0.5 reads 5.19 Hz on a published "
                "0.25..20 range, whose linear midpoint is 10.125 Hz"
            ),
        },
        "excluded_objects": EXCLUDED,
        "module_roster_size": len(roster),
        "notes": notes,
        "unbacked_modules": unbacked,
        "unregistered_blocks": unregistered,
        "blocks": blocks,
        "paramsets": paramsets,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--schema", type=Path, default=SCHEMA)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the committed file is not what this "
                         "run produces, without writing")
    args = ap.parse_args(argv)

    schema = json.loads(args.schema.read_text())
    registry = build(schema)
    text = json.dumps(registry, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist")
            return 1
        if args.out.read_text() != text:
            print(f"{args.out} is not what {args.schema} produces; re-run "
                  f"without --check")
            return 1
        print(f"{args.out} is current")
        return 0

    args.out.write_text(text)
    blocks, paramsets = registry["blocks"], registry["paramsets"]
    params = sum(len(s) for s in paramsets.values())
    print(f"wrote {args.out}")
    print(f"  {len(blocks)} objects, {len(paramsets)} distinct parameter sets, "
          f"{params} parameters, fingerprint {registry['schema_fingerprint']}")
    print(f"  {len(registry['unbacked_modules'])} roster entries with no object")
    print(f"  {len(registry['unregistered_blocks'])} patch objects with no ordinal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
