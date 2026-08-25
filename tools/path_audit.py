#!/usr/bin/env python3
"""End-to-end signal-path audit: proves each scene can make sound.

    python tools/path_audit.py 139 154

For every scene of every preset in the range, walks the actual routing
grid (cable masks) from the Input block to the Output block and checks
that a LIVE path exists:

- INPUT engaged (source blocks have no thru; bypassed = silence)
- every hop either engaged or bypassed-with-thru
- a FDBKRET hop only passes if ENGAGED (it has no grid input; its signal
  arrives over the send/return bus), and only if some FDBKSEND is
  reachable to feed that bus
- OUTPUT reachable and engaged

This is the check that distinguishes "the write landed" from "the scene
makes sound". It exists because five different silent-scene classes each
passed write-level verification: the severed Return, a severed cable, a
silently refused insert, and bypassed Input blocks. Wire verification
answers "did my edit stick"; this answers "is the tone alive end to end".
It cannot judge whether the tone is GOOD (that stays with ears), only
whether it exists.

Grid decode notes: cable_in_mask bit (row+1) set on cell (r, c) means it
is fed from row `row` at column c-1 (hardware-derived 2026-08-23 from
verified topologies). Grid effect ids alias mod 128 (protocol finding 2);
they are disambiguated against the status dump before classification.

Exit 1 with named dead scenes if any scene fails. Writes
kb/tone_library/path_audit.json.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402
# the walk itself lives in the library now; both this and the device use it
from fm9.signal_path import (  # noqa: E402,F401
    resolve_aliases, scene_alive, walk)



def main(a: int, b: int) -> int:
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    rows, dead = [], []
    with dev:
        for n in range(a, b + 1):
            dev.select_preset(n); time.sleep(0.4)
            cells = dev.read_grid() or []
            for sc in range(1, 9):
                dev.set_scene(sc); time.sleep(0.3)
                _, nm = dev.scene_name(sc)
                if nm.strip() in ("-", ""):
                    continue
                st = {x.effect_id: x for x in dev.status_dump() or []}
                ok, why = scene_alive(cells, st, reg)
                row = {"preset": n, "scene": sc, "name": nm,
                       "alive": ok, "why": why}
                rows.append(row)
                mark = "" if ok else f"  <- DEAD: {why}"
                print(f"{n} {sc}:{nm[:20]:20s} {'alive' if ok else 'DEAD'}{mark}")
                if not ok:
                    dead.append(row)
    out = ROOT / "kb" / "tone_library" / "path_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"rows": rows, "dead": dead}, out.open("w"), indent=1)
    print(f"\n{len(dead)} dead scenes -> {out}")
    return 0 if not dead else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), int(sys.argv[2])))
