#!/usr/bin/env python3
"""DSP budget advisor: will this block combination fit?

    python tools/budget_advisor.py DISTORT DISTORT DELAY DELAY PITCH REVERB

Evidence-based, no fake CPU model: the FM9 refuses over-budget inserts
SILENTLY (docs/PROTOCOL.md item 9), so the honest predictor is the
owner's own 512-preset library: has any professionally-built preset on
this very unit run this combination (or a superset)? If yes, it fits by
construction. If no preset in the collection runs it, an insert refusal
is likely and the advisor names the closest existing combos.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

LIB = ROOT / "kb" / "tone_library" / "presets.jsonl"

def block_multiset(rec, reg):
    fams = Counter()
    seen = set()
    for b in rec.get("blocks", []):
        if b["effect_id"] in seen:
            continue
        seen.add(b["effect_id"])
        fams[b["family"]] += 1
    return fams

def covers(candidate: Counter, want: Counter) -> bool:
    return all(candidate.get(f, 0) >= n for f, n in want.items())

def main(fams):
    if not LIB.exists():
        sys.exit("no tone library: run tools/harvest_tone_library.py first")
    reg = Registry()
    want = Counter(f.upper() for f in fams)
    fits, nearest = [], []
    for line in LIB.open():
        rec = json.loads(line)
        have = block_multiset(rec, reg)
        if covers(have, want):
            fits.append((rec["number"], rec["name"]))
        else:
            missing = sum((want - have).values())
            nearest.append((missing, rec["number"], rec["name"]))
    print(f"combo: {dict(want)}")
    if fits:
        print(f"FITS: {len(fits)} presets in your collection run this "
              f"combination or more. Examples:")
        for n, name in fits[:5]:
            print(f"  {n}: {name}")
    else:
        nearest.sort()
        print("NO preset in your collection runs this combination - an "
              "insert refusal is likely. Closest existing combos:")
        for missing, n, name in nearest[:5]:
            print(f"  {n}: {name} (short by {missing} block(s))")
    return 0 if fits else 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
