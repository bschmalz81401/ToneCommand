#!/usr/bin/env python3
"""Decode a ToneX serial control frame: the CLI over devices/tonex/frames.py.

    tools/tonex_decode.py kb/tone_library/tonex_captures/pc000.bin pc002.bin --diff

The grammar, the CRC and every finding live in devices/tonex/frames.py since
issue #163 moved the decoder under the adapter; the names below are
re-exported so existing tests and scripts keep working.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.tonex.frames import (  # noqa: E402,F401  re-exported on purpose
    CATEGORY_SLOT, DATE_SLOT, ESCAPE, FRAME_LEAD, NAME_SLOT, TAG_F32, TAG_STR,
    Frame, decode, diff, fcs, unstuff)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("frames", nargs="+", type=Path, help="captured .bin frames")
    ap.add_argument("--diff", action="store_true",
                    help="compare the first two frames slot by slot")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    decoded = [(p, decode(p.read_bytes())) for p in args.frames]

    if args.json:
        print(json.dumps({p.name: f.summary() for p, f in decoded}, indent=2))
        return

    for p, f in decoded:
        print(f"{p.name}: {f.name!r}  [{f.category}]  "
              f"{len(f.floats)} floats, {f.structural} undecoded bytes")

    if args.diff and len(decoded) >= 2:
        (pa, a), (pb, b) = decoded[0], decoded[1]
        d = diff(a, b)
        print(f"\n{pa.name} vs {pb.name}: {len(d)} float slot(s) differ")
        for i, x, y in d[:40]:
            print(f"  slot {i:3d}: {x:>12.4f}  ->  {y:>12.4f}")
        if not d:
            print("  identical control state (the tone is in the capture, "
                  "not in these floats)")


if __name__ == "__main__":
    main()
