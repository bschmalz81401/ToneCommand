#!/usr/bin/env python3
"""Extract the HeadRush normalisation tapers from the vendor's editor bundle.

    python tools/build_headrush_tapers.py --from-file main.js
    python tools/build_headrush_tapers.py --host headrushcore.local

#33, #126. Companion to tools/build_headrush_topologies.py and the same class
of source: read out of the vendor's own web editor, which the unit serves, and
NOT out of the API. The artifact says so, carrying `api_readable: false` and
`provenance: "vendor editor bundle"`.

WHAT A TAPER IS HERE, AND WHY THE DEVICE ALONE IS NOT ENOUGH

Continuous properties take 0..1 on the wire while their published `minimum`,
`maximum` and `format` describe the scale the unit SHOWS (#122, measured on a
Core by writing a value and reading the screen). The curve between the two is
per parameter. The DEVICE publishes an opaque integer for it,
`x-options.normalizeAlgo`, and never says what an integer denotes, which is why
`devices/headrush/registry.py` refuses to convert: a caller cannot evaluate a
name.

The vendor's editor has to convert, so it carries the formulas. This reads them
out, as a named enumeration of eleven and two lookup tables, one per direction.

NOTHING IS TRANSCRIBED BY EYE, WHICH IS THE POINT

A hand-copied formula is a guess that looks like a fact, and `Db` and
`AllenHeathFaderVolume` are exactly the shapes that survive a typo while
producing plausible numbers. So the generator does not read the maths and
retype it. It extracts the vendor's own source text, RUNS IT under node over a
deterministic grid, and commits the resulting reference vectors beside the
source. `devices/headrush/tapers.py` is then checked against those vectors
rather than against anyone's reading of the JavaScript.

That is also why node is a requirement to REGENERATE and not to use: the
committed artifact is the deliverable, exactly as the topology table is.

WHAT IS STILL NOT KNOWN

Which parameters the unit actually applies each algo to beyond what
`normalizeAlgo` says, whether the firmware's own conversion agrees with its
editor's in every case, and what the editor's `grid` quantisation does to a
value on the way in. Six readings off a Core agree with these formulas to the
digit (see `verify` below), which is corroboration on one block and not proof
across 302 objects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
OUT = CONFIG / "headrush_tapers.json"

SCHEMA_VERSION = 1

# The enum, the two tables, and the helpers they close over. Anchored on the
# vendor's own identifiers so a bundle that no longer contains them refuses
# rather than matching something else.
ENUM = re.compile(r"!function\(e\)\{(e\[e\.Linear=0\][^}]*?)\}\(kf\|\|\(kf=\{\}\)\)")
NORMALISE = re.compile(r"const xf=\{(.*?)\},Af=\{", re.S)
DENORMALISE = re.compile(r"\},Af=\{(.*?)\};function Lf", re.S)
HELPERS = re.compile(
    r"function yf\(e\)\{return Math\.pow\(10,\.05\*e\)\}"
    r"const vf=[^;]*;")
CLAMP = re.compile(r"function Ze\(e,t,n\)\{[^}]*\}")

# The grid the reference vectors are computed on. Endpoints because that is
# where a wrong formula is most visibly wrong, and irregular interior points so
# a table that happened to be right at halves would still be caught.
WIRE_POINTS = (0.0, 0.0001, 0.05, 0.25, 1 / 3, 0.5, 0.75, 0.9, 0.99, 0.999, 1.0)

# Ranges drawn from real properties rather than invented, so the vectors
# exercise the shapes the firmware actually publishes: a percentage, a
# symmetric dB, a decade-spanning frequency, a narrow time, a wide filter.
RANGES = (
    (0.0, 100.0),
    (-12.0, 12.0),
    (0.25, 20.0),
    (25.0, 1000.0),
    (1000.0, 16000.0),
    (-100.0, 100.0),
)

# Readings taken off a Core's screen, 2026-09-15 (#126). The generator checks
# the extracted code against these before writing anything, so a bundle whose
# maths disagrees with the hardware refuses instead of shipping.
HARDWARE = (
    # property,        algo, minimum, maximum, wire, format,     screen
    ("Amp.Bass",          0,     0.0,   100.0, 0.75, "%.0f %%",  "75 %"),
    ("Amp.Treble",        0,     0.0,   100.0, 0.5,  "%.0f %%",  "50 %"),
    ("Amp.PostGain",      0,   -12.0,    12.0, 0.5,  "%.1f dB",  "0.0 dB"),
    ("Amp.TremDepth",     0,     0.0,   100.0, 0.0,  "%.0f %%",  "0 %"),
    ("Amp.TremSpeed",     5,    0.25,    20.0, 0.5,  "%.2f Hz",  "5.19 Hz"),
    ("Amp.TremSpeed",     5,    0.25,    20.0, 0.25, "%.2f Hz",  "1.48 Hz"),
)


def fail(message: str) -> None:
    sys.exit(f"build_headrush_tapers: {message}")


def extract(bundle: str) -> dict[str, str]:
    """The vendor's own source text for each piece, or a refusal."""
    pieces = {}
    for name, pattern in (("enum", ENUM), ("normalise", NORMALISE),
                          ("denormalise", DENORMALISE), ("helpers", HELPERS),
                          ("clamp", CLAMP)):
        found = pattern.search(bundle)
        if not found:
            fail(f"the bundle does not contain the {name} the vendor's editor "
                 f"used at the firmware this was written against. It may have "
                 f"been rebuilt; read it before loosening this pattern.")
        pieces[name] = found.group(1) if found.groups() else found.group(0)
    return pieces


def names_from(enum_src: str) -> dict[int, str]:
    """`e[e.Squared=5]="Squared"` for each member, in the vendor's spelling."""
    names = {int(n): label for label, n, _q, label2 in
             re.findall(r'e\[e\.(\w+)=(\d+)\]=("?)(\w+)\3', enum_src)
             for _ in (0,) if label == label2}
    if not names:
        fail("the enum matched but no members parsed out of it")
    return names


def run_node(pieces: dict[str, str], names: dict[int, str]) -> dict:
    """Execute the vendor's functions to produce reference vectors.

    The alternative is reading the maths and retyping it in Python, which is
    the thing this file exists not to do.
    """
    cases = json.dumps([
        {"algo": a, "minimum": lo, "maximum": hi, "wire": x}
        for a in sorted(names)
        for lo, hi in RANGES
        for x in WIRE_POINTS
    ])
    harness = f"""
{pieces['clamp']}
{pieces['helpers']}
var kf;!function(e){{{pieces['enum']}}}(kf||(kf={{}}));
const xf={{{pieces['normalise']}}},Af={{{pieces['denormalise']}}};
const Lf=(e,t)=>Math.fround(((t.algo?xf[t.algo]:void 0)??xf[0])(e,t));
const If=(e,t)=>((t.algo?Af[t.algo]:void 0)??Af[0])(e,t);
const out=[];
for (const c of {cases}) {{
  const info={{minimum:c.minimum,maximum:c.maximum,algo:c.algo}};
  let display=null, roundTrip=null;
  try {{ display=If(c.wire,info); }} catch (e) {{ display=null; }}
  if (display!==null && Number.isFinite(display)) {{
    try {{ roundTrip=Lf(display,info); }} catch (e) {{ roundTrip=null; }}
  }}
  out.push({{...c, display, roundTrip}});
}}
process.stdout.write(JSON.stringify(out));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        path = fh.name
    try:
        done = subprocess.run(["node", path], capture_output=True, text=True)
    except FileNotFoundError:
        fail("node is not on PATH. It is needed to REGENERATE this table, "
             "because the vendor's own code is executed rather than "
             "transcribed. The committed file needs nothing to use.")
    finally:
        Path(path).unlink(missing_ok=True)
    if done.returncode != 0:
        fail(f"the vendor's own code did not run:\n{done.stderr.strip()}")
    return json.loads(done.stdout)


def verify(vectors: list[dict]) -> list[str]:
    """Check the extracted maths against readings taken off real hardware.

    A bundle that parses cleanly and disagrees with the unit is worse than one
    that does not parse, so this runs before anything is written.
    """
    index = {(v["algo"], v["minimum"], v["maximum"], v["wire"]): v["display"]
             for v in vectors}
    problems = []
    for prop, algo, lo, hi, wire, fmt, screen in HARDWARE:
        display = index.get((algo, lo, hi, wire))
        if display is None:
            problems.append(f"{prop}: no vector for algo {algo} at wire {wire}")
            continue
        shown = (fmt % display).replace("%%", "%")
        if shown != screen:
            problems.append(
                f"{prop}: the bundle's algo {algo} gives {shown!r} at wire "
                f"{wire}, and the unit's screen showed {screen!r}")
    return problems


def build(bundle: str, source: str) -> dict:
    pieces = extract(bundle)
    names = names_from(pieces["enum"])
    if len(names) != 11:
        fail(f"expected 11 tapers, parsed {len(names)}: {sorted(names)}")

    vectors = run_node(pieces, names)
    problems = verify(vectors)
    if problems:
        fail("the extracted formulas disagree with hardware readings, so "
             "nothing was written:\n  " + "\n  ".join(problems))

    # Which ids the two tables actually implement. DelayRatio (3) is in the
    # enum and in NEITHER table, so the vendor's own `?? xf[0]` sends it to
    # Linear. Recorded rather than quietly filed under Linear, because an id
    # with no implementation and an id implemented AS linear are different
    # facts about the firmware.
    in_table = {
        "to_display": sorted(
            n for n, label in names.items()
            if f"[kf.{label}]" in pieces["denormalise"]),
        "to_wire": sorted(
            n for n, label in names.items()
            if f"[kf.{label}]" in pieces["normalise"]),
    }
    unimplemented = sorted(set(names) - set(in_table["to_display"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "device": "headrush",
        "provenance": "vendor editor bundle",
        "api_readable": False,
        "source": source,
        "generated_by": "tools/build_headrush_tapers.py",
        "keyed_by": ("tapers by the integer the device publishes in "
                     "x-options.normalizeAlgo; vectors are a flat list keyed "
                     "by (algo, minimum, maximum, wire)"),
        "content": ("the eleven normalisation curves the vendor's editor uses "
                    "to convert between the 0..1 wire value and the value the "
                    "unit displays, with reference vectors computed by running "
                    "the vendor's own code"),
        "warning": (
            "NOT A DEVICE READ. The unit publishes an opaque integer per "
            "parameter (x-options.normalizeAlgo) and no formula; these come "
            "from the web editor the unit serves, which is the best available "
            "source and is still not the API. Anything surfacing a converted "
            "value to a user or a planner has to say so. Corroborated against "
            "six readings taken off a Core's screen, which is one block and "
            "not proof across 302 objects."
        ),
        "tapers": {str(n): {"name": names[n],
                            "implemented": n in in_table["to_display"]}
                   for n in sorted(names)},
        "absent_algo_is": 0,
        "absent_algo_note": (
            "the vendor's own dispatch is `(t.algo ? table[t.algo] : void 0) "
            "?? table[0]`, so a parameter with no normalizeAlgo, and equally "
            "one carrying 0, resolves to Linear"),
        "unimplemented": unimplemented,
        "unimplemented_note": (
            "in the enum and in neither table, so the vendor's fallback sends "
            "them to Linear. An id with no implementation and an id "
            "implemented as Linear are different facts, so they are named"),
        # WHAT IS AND IS NOT CARRIED. The names and the vectors are facts:
        # an enumeration the vendor publishes in a file it serves, and numbers
        # its own code produced. Those are recorded. The vendor's SOURCE TEXT
        # is not, because committing minified third-party JavaScript into this
        # repository would be redistributing their code, and no licence grants
        # that. A sha256 of each extracted fragment is kept instead, so a
        # regeneration can be shown to have read the same code without the
        # code travelling with it, and THIRD_PARTY_NOTICES.md records the
        # source. Anyone verifying the maths reads it from their own unit.
        "vendor_source_sha256": {
            name: hashlib.sha256(pieces[key].encode()).hexdigest()[:16]
            for name, key in (("to_display", "denormalise"),
                              ("to_wire", "normalise"),
                              ("helpers", "helpers"),
                              ("clamp", "clamp"))
        },
        "vendor_source_note": (
            "extracted from the unit's static/js/main.<hash>.js and NOT "
            "redistributed here; these are sha256 prefixes of the fragments "
            "this table was generated from. Re-read them from a unit with "
            "tools/build_headrush_tapers.py to check the maths."
        ),
        "hardware_check": [
            {"property": p, "algo": a, "minimum": lo, "maximum": hi,
             "wire": w, "format": f, "screen": s}
            for p, a, lo, hi, w, f, s in HARDWARE
        ],
        "vectors": vectors,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-file", type=Path,
                    help="a saved copy of the unit's static/js/main.<hash>.js")
    ap.add_argument("--host", help="fetch the bundle from a unit")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the committed file is not what this "
                         "run produces, without writing")
    args = ap.parse_args(argv)

    if args.from_file:
        bundle = args.from_file.read_text(errors="replace")
        source = f"vendor editor bundle, {args.from_file.name}"
    elif args.host:
        from devices.headrush.client import HeadrushClient  # noqa: local import
        fail("fetching from a unit is not implemented here; save the bundle "
             "with tools/build_headrush_topologies.py and pass --from-file")
    else:
        fail("pass --from-file with the unit's editor bundle")

    table = build(bundle, source)
    text = json.dumps(table, indent=2, sort_keys=True) + "\n"

    if args.check:
        if not args.out.exists() or args.out.read_text() != text:
            print(f"{args.out} is not what this bundle produces")
            return 1
        print(f"{args.out} is current")
        return 0

    args.out.write_text(text)
    named = table["tapers"]
    print(f"wrote {args.out}")
    print(f"  {len(named)} tapers, {len(table['vectors'])} reference vectors")
    print(f"  unimplemented (fall back to Linear): {table['unimplemented']}")
    print(f"  all {len(HARDWARE)} hardware readings reproduced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
