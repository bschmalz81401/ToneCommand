#!/usr/bin/env python3
"""Snapshot the ten HeadRush signal-path templates to config/headrush_topologies.json.

    python tools/build_headrush_topologies.py                      # headrushcore.local
    python tools/build_headrush_topologies.py --host 10.8.72.116
    python tools/build_headrush_topologies.py --from-file main.js  # regenerate offline

#33 phase 3, the data half. Companion to tools/build_headrush_schema.py, and
deliberately a SEPARATE artifact from it, because the two have different
provenance and conflating them would hide that.

WHY THIS IS NOT IN THE SCHEMA SNAPSHOT

The device publishes `Chain.Routing` as an integer 0..9 with ten names, and
that is all it publishes. It does not say what any of them MEAN. Verified on
the unit (#109): writing each of the ten in turn and reading the whole chain
object back leaves every per-slot property byte-identical, so the names are
machine readable and the shapes are not.

    "Routing": {"type": "integer", "minimum": 0.0, "maximum": 9.0,
                "x-options": {"strings": ["S", "SPS-1", ... ]}}

So a simulator that only had the schema could offer the ten names and could
not tell a caller that `SPS-1` splits after slot 3 and rejoins before slot 12.
That is the difference between choosing a topology and knowing what you chose.

WHERE THE SHAPES COME FROM, AND WHY THAT IS SAID LOUDLY

The unit serves its own web editor, and that bundle carries a layout table:
per routing an `index`, `name`, `displayOrder`, `requiresVocals`, the IO blocks
it draws, and fourteen slots each with a screen position and a `roleInChain`.
This reads that table.

It is the vendor's own code, which is the best available source and is still
NOT the device's API. Everything generated here is therefore marked
`provenance: "vendor editor bundle"` and `api_readable: false`, and the
simulator must present it as declared knowledge rather than a device read. An
adapter that answered "where are the branches" as though it had asked the unit
would be claiming a read path it does not have, which is the exact failure
`Capabilities` exists to prevent.

WHAT roleInChain MEANS, MEASURED

    0   common: on the single path, or before/after a split
    1   the first parallel branch
    2   the second parallel branch

and the two traps in it:

  - It is **0 on all fourteen slots of every dual topology**. Duals are not
    branches of one path, they are two independent paths that never meet, each
    with its own input and output. They are told apart by the IO block list
    carrying two pairs, not by roleInChain.
  - Index order is NOT the order the picker draws them. `displayOrder` is a
    separate field, and index 5 and index 9 are drawn sixth and fifth. Anything
    writing `Routing` from a menu position selects the wrong topology.

REFUSES RATHER THAN GUESSES. If fewer than ten definitions parse, or a routing
carries other than fourteen slots, or the names disagree with the committed
schema's own `Routing` enumeration, this exits non-zero and writes nothing. A
topology table that silently lost a branch would be worse than none, because
everything downstream would place blocks into it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "headrush_topologies.json"
SCHEMA = ROOT / "config" / "headrush_schema.json"

DEFAULT_HOST = "headrushcore.local"
SLOTS = 14
ROUTINGS = 10

#: The bundle is minified, so these match its emitted object literals rather
#: than source. Each is anchored on a field the minifier does not rename
#: because it is a data key, not an identifier.
_DEF = re.compile(r'\{index:(\d+),name:"([^"]+)"')
_SLOT = re.compile(r'\{position:\[(-?\d+),(-?\d+)\],roleInChain:(\d+)\}')
_DISPLAY = re.compile(r"displayOrder:(\d+)")
_VOCALS = re.compile(r"requiresVocals:!(\d)")
_IOBLOCK = re.compile(r'ioBlockName:"([^"]+)"')
_INPUTTYPE = re.compile(r'inputType:"([^"]+)"')
_SCRIPT = re.compile(r'src="(static/js/main\.[0-9a-f]+\.js)"')

#: roleInChain, as measured. Named so the simulator never carries bare ints.
COMMON, BRANCH_A, BRANCH_B = 0, 1, 2


def fetch(host: str, timeout: float) -> tuple[str, str]:
    """The editor bundle, and the path it came from.

    The filename carries a content hash that changes with firmware, so it is
    discovered from the index rather than pinned. Pinning it would turn a
    firmware update into a 404 that looks like the unit being offline.
    """
    base = f"http://{host}"
    with urllib.request.urlopen(base + "/", timeout=timeout) as resp:
        index = resp.read().decode("utf-8", "replace")
    hit = _SCRIPT.search(index)
    if not hit:
        sys.exit(f"{base}/ served no static/js/main.<hash>.js script tag; "
                 f"is this a HeadRush editor?")
    path = hit.group(1)
    with urllib.request.urlopen(f"{base}/{path}", timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace"), path


def parse(bundle: str) -> list[dict]:
    """The ten routing definitions, in index order."""
    heads = [(m.start(), int(m.group(1)), m.group(2))
             for m in _DEF.finditer(bundle)]
    out = []
    for i, (start, index, name) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(bundle)
        chunk = bundle[start:end]
        slots = [
            {"slot": n, "position": [int(x), int(y)], "role": int(role)}
            for n, (x, y, role) in enumerate(
                ((m.group(1), m.group(2), m.group(3))
                 for m in _SLOT.finditer(chunk)), start=1)
        ]
        display = _DISPLAY.search(chunk)
        vocals = _VOCALS.search(chunk)
        out.append({
            "index": index,
            "name": name,
            "display_order": int(display.group(1)) if display else None,
            # requiresVocals is minified to !0 (true) / !1 (false)
            "requires_vocals": (vocals.group(1) == "0") if vocals else None,
            "io_blocks": _IOBLOCK.findall(chunk),
            "input_types": _INPUTTYPE.findall(chunk),
            "slots": slots,
        })
    return out


def _cluster(values: list[int]) -> list[list[int]] | None:
    """Split a sorted axis wherever the gap is much larger than the pitch.

    The editor lays slots on a regular pitch within a path and leaves a wide
    margin between paths, so a gap several times the usual spacing is a path
    boundary. Returns None when there is no such gap, which means this axis
    does not separate the paths and the caller should try the other one.
    """
    uniq = sorted(set(values))
    if len(uniq) < 2:
        return None
    gaps = [b - a for a, b in zip(uniq, uniq[1:])]
    pitch = min(gaps)
    wide = [i for i, g in enumerate(gaps) if g > pitch * 1.8]
    if len(wide) != 1:
        return None
    cut = uniq[wide[0]]
    return [[v for v in uniq if v <= cut], [v for v in uniq if v > cut]]


def paths_of(routing: dict) -> tuple[list[list[int]], str]:
    """Which slots belong to which independent PATH, and how that was decided.

    Duals report role 0 on every slot, so the branch field cannot separate
    them; a routing drawing two input/output pairs is two paths. WHERE the
    fourteen divide is then read off the editor's own geometry rather than out
    of the routing's name.

    Geometry, not the name, because the name does not always carry it:
    `Dual Path 4-10` states the partition and `Dual Straight Path` states
    nothing. An earlier version of this fell back to SLOTS // 2 for that case,
    which is a guess wearing a measurement's clothes, and it would have shipped
    an unlabelled constant inside a file that presents itself as a snapshot.

    The two axes are both needed. On `Dual Path 4-10` the paths sit side by
    side, so the x positions carry the gap; on `Dual Straight Path` they are
    stacked and every x is shared, so the split is in y. Whichever axis
    separates them is recorded, and where the name DOES state a partition it
    is used as a cross-check rather than as the source.
    """
    ios = routing["io_blocks"]
    slots = routing["slots"]
    if len([b for b in ios if b.lower().startswith("input")]) < 2:
        return [[s["slot"] for s in slots]], "single path"

    for axis, name in ((0, "x"), (1, "y")):
        values = [s["position"][axis] for s in slots]
        groups = _cluster(values)
        if groups is None and axis == 1 and len(set(values)) == 2:
            # A two-row layout has exactly two y values and no third to
            # measure a pitch against, so the gap rule cannot see it. Only
            # reached when x has already failed, which is what keeps the
            # DISPLAY WRAP from being mistaken for a split: on Dual Path 4-10
            # the two paths sit side by side and x separates them first, and
            # its two rows are the same wrap every long single path uses.
            lo, hi = sorted(set(values))
            groups = [[lo], [hi]]
        if not groups:
            continue
        first = sorted(s["slot"] for s in slots if s["position"][axis] in groups[0])
        second = sorted(s["slot"] for s in slots if s["position"][axis] in groups[1])
        if not first or not second:
            continue
        stated = [int(n) for n in re.findall(r"\b(\d+)\b", routing["name"])]
        pair = [n for n in stated if 0 < n < SLOTS]
        if len(pair) == 2 and sum(pair) == SLOTS and pair[0] != len(first):
            sys.exit(
                f"routing {routing['index']} ({routing['name']!r}): the "
                f"editor's {name} geometry splits {len(first)}/{len(second)} "
                f"but the name states {pair[0]}/{pair[1]}. One of them is "
                f"wrong and guessing which would defeat the point.")
        method = f"{name} geometry"
        if len(pair) == 2:
            method += ", agrees with the name"
        return [first, second], method

    sys.exit(f"routing {routing['index']} ({routing['name']!r}) draws two "
             f"input/output pairs but neither axis separates its slots; the "
             f"partition cannot be measured and will not be guessed.")


def build(bundle: str, source_path: str, host: str | None) -> dict:
    routings = parse(bundle)
    if len(routings) != ROUTINGS:
        sys.exit(f"parsed {len(routings)} routing definitions, expected "
                 f"{ROUTINGS}. The bundle's shape has changed; fix the parser "
                 f"rather than lowering the expectation.")

    names = [r["name"] for r in routings]
    for r in routings:
        if len(r["slots"]) != SLOTS:
            sys.exit(f"routing {r['index']} ({r['name']!r}) carries "
                     f"{len(r['slots'])} slots, expected {SLOTS}")
        if r["display_order"] is None or r["requires_vocals"] is None:
            sys.exit(f"routing {r['index']} ({r['name']!r}) is missing "
                     f"displayOrder or requiresVocals")

    # The device's own enumeration is the authority on how many there are and
    # what order they sit in. A bundle that disagreed would mean the two were
    # fetched from different firmwares.
    enum = schema_routing_names()
    if len(enum) != len(routings):
        sys.exit(f"the committed schema enumerates {len(enum)} routings and "
                 f"the bundle defines {len(routings)}; they are not the same "
                 f"firmware.")

    for r in routings:
        r["paths"], r["partition_method"] = paths_of(r)
        roles = [s["role"] for s in r["slots"]]
        r["has_parallel_branches"] = BRANCH_A in roles
        r["independent_paths"] = len(r["paths"])
        r["schema_name"] = enum[r["index"]]

    return {
        "schema_version": 1,
        "device": "headrush",
        "content": "the ten signal-path templates: which slots are common, "
                   "which are on a parallel branch, and which belong to an "
                   "independent path",
        "keyed_by": "Chain.Routing integer, 0..9",
        "provenance": "vendor editor bundle",
        "api_readable": False,
        "source": f"GET http://<unit>/{source_path}, the unit's own web editor",
        "source_host": host,
        "source_sha256": hashlib.sha256(bundle.encode()).hexdigest()[:16],
        "generated_by": "tools/build_headrush_topologies.py",
        "role_values": {"0": "common", "1": "branch A", "2": "branch B"},
        "warning": "NOT a device read. The unit publishes the ten NAMES on "
                   "Chain.Routing and nothing about their shapes: writing each "
                   "in turn leaves every per-slot property byte-identical "
                   "(#109). These shapes are read out of the vendor's own "
                   "editor, which is the best available source and is still "
                   "not the API. Anything built on this must present it as "
                   "declared knowledge, never as something the device "
                   "answered.",
        "notes": [
            "roleInChain is 0 on all fourteen slots of every dual topology. "
            "Duals are independent paths, not branches, and are identified by "
            "the routing drawing two input/output pairs.",
            "display_order is not index order: index 5 and index 9 are drawn "
            "sixth and fifth. Writing Routing from a menu position selects the "
            "wrong topology.",
            "the slot partition for dual paths is measured off the editor's "
            "own slot geometry, because roleInChain cannot express it and the "
            "routing names do not always state it (Dual Straight Path states "
            "nothing). Each routing records which axis carried the split in "
            "partition_method, and where a name does state a partition it is "
            "cross-checked rather than trusted as the source.",
        ],
        "routings": routings,
    }


def schema_routing_names() -> list[str]:
    """The device's own Routing enumeration, from the committed schema."""
    if not SCHEMA.exists():
        sys.exit(f"{SCHEMA} is missing; run tools/build_headrush_schema.py first")
    blob = json.loads(SCHEMA.read_text(encoding="utf-8"))
    chain = blob["paths"].get("/Evil/Engine/Patch/Chain")
    if not chain:
        sys.exit("the committed schema has no /Evil/Engine/Patch/Chain")
    meta = blob["metas"][chain]
    props = meta.get("properties", meta)
    routing = props.get("Routing") or {}
    names = (routing.get("x-options") or {}).get("strings")
    if not names:
        sys.exit("the committed schema's Chain.Routing publishes no options")
    return list(names)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--from-file", type=Path,
                    help="a saved bundle, to regenerate without a unit")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    if args.from_file:
        bundle = args.from_file.read_text(errors="replace", encoding="utf-8")
        built = build(bundle, args.from_file.name, None)
    else:
        bundle, path = fetch(args.host, args.timeout)
        built = build(bundle, path, args.host)

    args.out.write_text(json.dumps(built, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    n = len(built["routings"])
    par = sum(1 for r in built["routings"] if r["has_parallel_branches"])
    dual = sum(1 for r in built["routings"] if r["independent_paths"] > 1)
    print(f"wrote {args.out.relative_to(ROOT)}: {n} routings, "
          f"{par} with parallel branches, {dual} with independent paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
