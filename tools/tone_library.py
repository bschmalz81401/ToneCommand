#!/usr/bin/env python3
"""Query the private tone library harvested from the device.

    python tools/tone_library.py amp 179        # who uses Texas Star Clean
    python tools/tone_library.py name worship   # presets matching a name
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

LIB = ROOT / "kb" / "tone_library" / "presets.jsonl"

def load():
    if not LIB.exists():
        sys.exit("no tone library yet: run tools/harvest_tone_library.py "
                 "with the FM9 on")
    return [json.loads(l) for l in LIB.open() if l.strip()]

def amp_ordinal(rec, reg):
    for b in rec["blocks"]:
        if b["family"] == "DISTORT":
            stride = len(b["params_wire"]) // b["channels"]
            return b["params_wire"][b["channel"] * stride + 10]
    return None

def main(argv):
    reg = Registry()
    recs = load()
    if argv[:1] == ["amp"] and len(argv) > 1:
        want = int(argv[1])
        hits = [r for r in recs if amp_ordinal(r, reg) == want]
        print(f"{len(hits)} presets use {reg.amp_description(want)}:")
        for r in hits:
            print(f"  {r['number']}: {r['name']}")
    elif argv[:1] == ["name"] and len(argv) > 1:
        pat = argv[1].lower()
        for r in recs:
            if pat in r["name"].lower():
                fams = [b["family"] for b in r["blocks"]]
                print(f"  {r['number']}: {r['name']}  [{', '.join(fams)}]")
    else:
        print(__doc__)

if __name__ == "__main__":
    main(sys.argv[1:])
