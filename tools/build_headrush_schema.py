#!/usr/bin/env python3
"""Snapshot a HeadRush unit's own self-description to config/headrush_schema.json.

    python tools/build_headrush_schema.py                      # headrushcore.local
    python tools/build_headrush_schema.py --host 10.8.72.116
    python tools/build_headrush_schema.py --from-file raw.json  # re-generate offline

#33 phase 2. The unit publishes its entire object tree over HTTP with property
types, real-unit ranges and enumerations, so this is a fetch and a reshape, not
a decode. That is the whole reason #8 called HeadRush the easy second device,
and it is what lets phases 3 and 4 be reviewed by someone with no HeadRush in
the room: the simulator is built from this file, not from the hardware.

WHAT IS COMMITTED, AND WHAT IS DELIBERATELY NOT.

`GET /api/v1/subtree/` answers with a `meta` AND a `value` for all 309 objects.
The meta is the device's schema and is the point of this file. The value is
whatever the owner's unit is doing at that instant: amp gain, which cab is
loaded, the current rig's fourteen slots. That is personal preset data, it
changes whenever a knob moves, and AGENTS.md says not to commit it. So values
are dropped by default and a short ALLOWLIST below carries back the handful
that are device CAPABILITY rather than owner state, each with its reason.
Deny-by-default, the same posture as the store whitelist and the send guard,
for the same reason: the safe direction is to publish less than you could.

REFUSES RATHER THAN GUESSES. If the firmware string cannot be read, or an
allowlisted roster is missing or the wrong shape, this exits non-zero and
writes nothing. A schema snapshot that quietly omits the model roster would be
worse than no snapshot, because everything downstream would treat it as
complete. Every DATA field is copied from the unit or computed from what the
unit said. The prose fields (source, support_status, content, keyed_by, warning
and notes) are authored, and the measured ones among them are derived from the
tree in hand rather than typed, so they cannot go stale against the data they
sit next to.

METAS ARE STORED ONCE PER DISTINCT META, keyed by a content hash, with a
path -> hash map beside them. That is a lossless deduplication and not a
twin-specific transform, which matters because the sharing is NOT only twins.
The numbers below are one firmware's and are repeated in the artifact's own
notes, where they are DERIVED rather than typed, so the generated file cannot go
stale against them even though this comment can.
Measured on fw 5.1.0.2a63755: 309 objects reduce to 161 distinct metas, and ten
of the sharing groups are not `base`/`base_2` pairs at all. `Green_JRC-OD` and
`Greener` share one meta across ten paths; so do `Black_Wah` and `Shine_Wah`,
`Amp_Clone` and `Pedal_Clone`. Different models, identical parameter surfaces.
A consumer cannot use "what parameters does this block have" as an identity for
"which model is this", and expressing the file this way makes that visible
instead of burying it in 1.7 MB of repetition.

ON TWINS, CORRECTING THE RECORD. #109 says the `_2` objects are "the twin the
schema marks with `twin: true`". That is not true of this API. Neither
`/api/v1/subtree` nor `/api/v1/object-meta` carries any twin field. The subtree
half is checked by the suite against the committed file; the object-meta half
was compared by hand against the unit on fw 5.1.0.2a63755, came back
byte-identical for the same path, and NOTHING here re-checks it.
The only occurrence of the string "twin" in the entire 2.2 MB tree is a cabinet
name, "California Twin 212 Combo". Whatever marks twins lives in the editor
bundle, not on the API, so this file records the sharing it MEASURED and makes
no claim about a flag.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devices.headrush.client import HeadrushClient  # noqa: E402

OUT = ROOT / "config" / "headrush_schema.json"
SCHEMA_VERSION = 1
DEFAULT_HOST = "headrushcore.local"

#: The firmware identity. Its own object, and the snapshot is worthless without
#: it: every fact in this file is true of ONE firmware, and #33 already agreed
#: to treat Prime and Flex Prime as untested rather than assumed.
FIRMWARE_PATH = "/Evil/Gui"
FIRMWARE_PROPERTY = "AppVersion"

#: Values that are device capability rather than owner state. Nothing else's
#: value is written. Each entry says why it is not personal data, because that
#: judgement is the only thing standing between this file and someone's rig.
ALLOWLIST: dict[str, dict[str, str]] = {
    "/Evil/API/Blocks": {
        # The roster Chain.ModuleType{n} indexes into. 278 entries, element 0
        # "Empty Slot" (#109). Fixed by firmware; it is what the unit CAN hold,
        # never what it currently holds.
        "ModuleTypes": "the block roster ModuleType{n} indexes into",
        "BlockSelectorCategories": "the block category vocabulary",
        "BlockSelectorCategoriesForDisplay": "the same categories as shown on the unit",
    },
    FIRMWARE_PATH: {
        FIRMWARE_PROPERTY: "the firmware every other fact here is true of",
    },
}

#: Shape checks for the allowlisted rosters. A roster that came back empty, or
#: as the wrong type, means the fetch half-failed and the snapshot must not be
#: written. Checked rather than trusted.
ROSTER_MUST_BE_NONEMPTY_LIST = {
    ("/Evil/API/Blocks", "ModuleTypes"),
    ("/Evil/API/Blocks", "BlockSelectorCategories"),
    ("/Evil/API/Blocks", "BlockSelectorCategoriesForDisplay"),
}


class Refused(RuntimeError):
    """The snapshot cannot be written honestly, so it is not written at all."""


def _meta_hash(meta) -> str:
    canonical = json.dumps(meta, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _notes(firmware, paths, metas, shared, rosters) -> list[str]:
    """The measured notes, DERIVED from the tree in hand rather than typed.

    An earlier draft wrote "ten sharing groups", "0..277" and the California
    Twin cab name as prose. Those are properties of one firmware, not of this
    script, so the next regeneration would have updated `firmware` and left the
    sentences describing the previous unit while still saying the file was
    generated and never hand edited. A second, stale source of hardware truth
    sitting next to the data is the failure this project has already rejected a
    change for, so every measured sentence below is computed.
    """
    not_twin = [g for g in shared if not (len(g) == 2 and g[1] == g[0] + "_2")]
    example = next((g for g in sorted(not_twin, key=lambda g: -len(g))), [])
    chain = metas.get(paths.get("/Evil/Engine/Patch/Chain", ""), {})
    slot = (chain.get("properties") or {}).get("ModuleType1") or {}
    slot_range = (
        f"an integer {int(slot['minimum'])}..{int(slot['maximum'])} with no names of its own"
        if "minimum" in slot and "maximum" in slot else "not present in this tree"
    )
    roster = rosters.get("/Evil/API/Blocks", {}).get("ModuleTypes") or []
    twin_strings = sorted({
        s for meta in metas.values()
        for s in json.dumps(meta).split('"') if "twin" in s.lower()
    })

    return [
        "PROPERTY VALUES ARE NOT IN THIS FILE, except the allowlist below. The "
        "subtree response carries the owner's live rig state beside the schema, "
        "and that is personal preset data AGENTS.md says not to commit. On the "
        "capture this file was built from, the tree held rig names, rig ids and "
        "setlist names under /Evil/API/Rigs and /Evil/API/Setlists; none of it "
        "is here, because the default is to drop.",
        "allowlisted values, each device capability rather than owner state: "
        + "; ".join(f"{p}.{n} ({why})"
                    for p, ws in sorted(ALLOWLIST.items())
                    for n, why in sorted(ws.items())),
        "metas are stored once per DISTINCT meta, keyed by a 16 hex char sha256 "
        "of the canonical JSON, with paths mapping to them. A hash that already "
        "holds a different meta is refused, so the dedup is lossless by check "
        "and not by assertion.",
        f"measured on fw {firmware}: {len(paths)} objects reduce to {len(metas)} "
        f"distinct metas, and {len(not_twin)} of the {len(shared)} sharing groups "
        "are NOT base/base_2 pairs"
        + (f", the largest being {example}" if example else "")
        + ". Different models, identical parameter surfaces, so a parameter set "
        "is not an identity for a model and a consumer must not treat it as one.",
        f"measured on fw {firmware}: Chain.ModuleType{{n}} is {slot_range}; the "
        f"names are the ModuleTypes roster, whose {len(roster)} entries begin "
        + (f"with {roster[0]!r}." if roster else "nowhere."),
        "this API does NOT mark twins. Issue #109 says the _2 objects carry "
        "twin: true; no meta in this file has any such field. The strings "
        "containing 'twin' in the metas of this capture are "
        + (", ".join(repr(s) for s in twin_strings) if twin_strings else "none")
        + ", which are model names. Separately, and NOT checked by this script: "
        "/api/v1/object-meta was compared against /api/v1/subtree by hand on "
        f"fw {firmware} and returned byte-identical meta for the same path.",
    ]


def build(tree: dict) -> dict:
    """Reshape one subtree response into the committed artifact.

    Pure, so the whole generator is testable from a fixture with no unit on the
    network, which is the same reason phase 1 injects its resolver and opener.
    """
    if not isinstance(tree, dict) or not tree:
        raise Refused("the subtree response was empty or not an object")

    firmware_node = tree.get(FIRMWARE_PATH)
    if not isinstance(firmware_node, dict):
        raise Refused(f"no {FIRMWARE_PATH} object, so the firmware cannot be named")
    firmware_value = firmware_node.get("value")
    # The same `or {}` hole the allowlist loop below has a comment and a test
    # for, thirty lines earlier and missed by both. Measured: a list, an int or
    # a string value makes the .get() an AttributeError, while [] and None fall
    # through `or {}` and reach the "firmware is None" refusal by luck rather
    # than by check. main() catches only Refused, so the first three are a
    # traceback where the operator needed "nothing written". Found by Grok on
    # the second review pass, after the first fix closed the copy further down.
    if not isinstance(firmware_value, dict):
        raise Refused(
            f"{FIRMWARE_PATH} has a {type(firmware_value).__name__} value, expected an object"
        )
    firmware = firmware_value.get(FIRMWARE_PROPERTY)
    if not isinstance(firmware, str) or not firmware.strip():
        raise Refused(
            f"{FIRMWARE_PATH}.{FIRMWARE_PROPERTY} is {firmware!r}; every fact in this "
            "file is true of one firmware and an unlabelled snapshot is not usable"
        )

    metas: dict[str, dict] = {}
    paths: dict[str, str] = {}
    for path in sorted(tree):
        node = tree[path]
        if not isinstance(node, dict) or "meta" not in node:
            raise Refused(f"{path} has no meta; the subtree response is not the shape this reads")
        digest = _meta_hash(node["meta"])
        # setdefault would silently keep the first meta and file a different one
        # under its hash, which is exactly the way a "lossless" dedup stops
        # being one. 16 hex chars is 64 bits and a collision across 161 items is
        # negligible, but negligible is not a reason to leave it unchecked.
        if digest in metas and metas[digest] != node["meta"]:
            raise Refused(f"meta hash collision at {digest} on {path}; the dedup would not be lossless")
        metas[digest] = node["meta"]
        paths[path] = digest

    rosters: dict[str, dict] = {}
    for path, wanted in ALLOWLIST.items():
        node = tree.get(path)
        if not isinstance(node, dict):
            raise Refused(f"allowlisted object {path} is absent from the tree")
        value = node.get("value")
        # Not `or {}`. Measured: every non-dict value makes the subscript below
        # a TypeError rather than a Refused, and main() only catches Refused, so
        # the operator would get a traceback instead of the "nothing written"
        # that tells them the snapshot is not on disk.
        if not isinstance(value, dict):
            raise Refused(f"{path} has a {type(value).__name__} value, expected an object")
        for name in sorted(wanted):
            if name not in value:
                raise Refused(f"allowlisted value {path}.{name} is absent")
            got = value[name]
            if (path, name) in ROSTER_MUST_BE_NONEMPTY_LIST:
                # A list of the wrong thing is the "looks complete, slot names
                # are gone" failure with extra steps, so the contents are
                # checked and not only the container.
                if not isinstance(got, list) or not got:
                    raise Refused(f"{path}.{name} is {type(got).__name__}, expected a non-empty list")
                bad = [i for i, entry in enumerate(got)
                       if not isinstance(entry, str) or not entry.strip()]
                if bad:
                    raise Refused(
                        f"{path}.{name} has {len(bad)} entries that are not non-empty "
                        f"strings, first at index {bad[0]}"
                    )
            rosters.setdefault(path, {})[name] = got

    shared = sorted(
        (sorted(p for p, d in paths.items() if d == digest) for digest in metas
         if sum(1 for d in paths.values() if d == digest) > 1),
        key=lambda group: group[0],
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "device": "headrush",
        "firmware": firmware,
        "support_status": (
            "measured on a HeadRush Core at this firmware only. Prime and Flex Prime "
            "are one engine per the spec and are UNTESTED, not assumed (#33)."
        ),
        "source": (
            "the unit's own self-description, GET /api/v1/subtree/ over HTTP on port 80, "
            "fetched by tools/build_headrush_schema.py through devices/headrush/client.py. "
            "Generated, never hand edited."
        ),
        "generated_by": "tools/build_headrush_schema.py",
        "content": (
            "the device's property schema: every object's type, property types, "
            "real-unit ranges and enumerations. NOT its current settings."
        ),
        "keyed_by": (
            "paths maps an object path to a meta hash; metas maps that hash to the "
            "meta itself; rosters is keyed by object path then property name"
        ),
        "warning": (
            "one firmware, one chassis. Re-generate rather than edit; the file is "
            "deterministic, so a re-run on the same firmware is a no-op diff and a "
            "re-run on new firmware is a readable one."
        ),
        "notes": _notes(firmware, paths, metas, shared, rosters),
        "object_count": len(paths),
        "distinct_meta_count": len(metas),
        "shared_meta_groups": shared,
        "rosters": rosters,
        "paths": paths,
        "metas": metas,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"unit hostname (default {DEFAULT_HOST})")
    ap.add_argument("--from-file", type=Path, help="re-generate from a saved subtree response")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--save-raw", type=Path, help="also write the untouched subtree response here")
    args = ap.parse_args(argv)

    if args.from_file:
        tree = json.loads(args.from_file.read_text(encoding="utf-8"))
        where = str(args.from_file)
    else:
        client = HeadrushClient.connect(args.host, timeout_s=120.0)
        where = f"{args.host} ({client.address})"
        print(f"fetching the whole tree from {where} ...", file=sys.stderr)
        tree = client.subtree("")

    try:
        artifact = build(tree)
    except Refused as why:
        print(f"REFUSED: {why}", file=sys.stderr)
        print(f"nothing written to {args.out}.", file=sys.stderr)
        return 1

    # AFTER build(), and said out loud. The raw subtree is the whole response,
    # live values included, which on the capture this was written from meant
    # 9055 property values, the owner's rig names and their setlist names. It
    # used to be written before build(), so a refused run wrote a file full of
    # preset data and then printed "nothing written", which was false about the
    # one file that most needed saying.
    if args.save_raw:
        args.save_raw.write_text(json.dumps(tree, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            f"WARNING: wrote the untouched response to {args.save_raw}. It contains the "
            "unit's LIVE VALUES, including rig and setlist names. Do not commit it.",
            file=sys.stderr,
        )

    args.out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # relative_to raises for an --out outside the repo, which the offline
    # regeneration check uses, so this reports rather than throwing on the way
    # out of a run that already succeeded.
    try:
        shown = args.out.relative_to(ROOT)
    except ValueError:
        shown = args.out
    print(
        f"wrote {shown}: {artifact['object_count']} objects, "
        f"{artifact['distinct_meta_count']} distinct metas, fw {artifact['firmware']}, "
        f"from {where}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
