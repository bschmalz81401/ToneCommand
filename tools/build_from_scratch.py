#!/usr/bin/env python3
"""Build a working chain into an empty preset slot, from nothing.

    python tools/build_from_scratch.py                 # pick a free slot
    python tools/build_from_scratch.py --slot 386       # a specific free one
                                                       # (WIRE number; FM9-Edit calls it 387)
    python tools/build_from_scratch.py --range 386 444  # search only this band
    TONECOMMAND_SIM=1 python tools/build_from_scratch.py

An empty FM9 slot is emptier than it looks: no grid cells at all, and no
Input or Output blocks either. There is nothing to splice into, so this
places the whole chain and draws every cable itself.

ALWAYS lands on a slot the device itself reports as <EMPTY>, and refuses
outright when there is no free slot to build on. It will not pick a
preset someone owns, and it takes no --force.

EDIT BUFFER ONLY. Nothing is stored, so the slot's stored name stays
<EMPTY> and re-selecting any preset discards the build. Storing is a
separate, whitelisted operation on purpose.

The blocks arrive at factory defaults and will sound plain until voiced.
Audible is the claim being made here, not good: verify with your ears,
which outrank every read path this tool has (docs/PROTOCOL.md, finding 13).
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9 import protocol as p  # noqa: E402
from fm9.registry import Registry  # noqa: E402
from tools.path_audit import scene_alive  # noqa: E402

# Consecutive columns on display row 3: cables only ever run to the next
# column, and shunts cannot be inserted (PROTOCOL.md finding 8), so a gap
# would need a unity Volume block as a hop rather than a shunt.
ROW = 3
CHAIN = [(37, "INPUT"), (58, "amp"), (62, "cab"), (42, "OUTPUT")]
SETTLE = 0.4


def describe(cells, reg) -> list[str]:
    """Render a grid already read. Re-reading here would cost a round trip and,
    on a timed-out second read, print an empty grid under a success line."""
    lines = []
    for c in sorted(cells, key=lambda c: (c.col, c.row)):
        fam = reg.family_of_effect_id(c.effect_id or 0)
        name = "SHUNT" if c.is_shunt else (fam[0] if fam else f"eid{c.effect_id}")
        lines.append(f"  row {c.row + 1} col {c.col + 1}: {name:9s} "
                     f"in_mask={c.cable_in_mask:#06b}")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--slot", type=int, default=None,
                    help="build here (WIRE number 0-511, which FM9-Edit shows "
                         "as 1-512); must be empty, or the run refuses")
    ap.add_argument("--range", type=int, nargs=2, metavar=("START", "END"),
                    default=[0, 511],
                    help="wire slots to search (default 0 511)")
    args = ap.parse_args(argv)

    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)

    from fm9.device import FM9NotFound, NoEmptySlot
    with dev:
        held = dev.current_preset()
        try:
            target = (dev.require_empty_slot(args.slot) if args.slot is not None
                      else dev.first_empty_slot(*args.range))
        except (NoEmptySlot, ValueError, FM9NotFound) as exc:
            print(f"refusing to build: {exc}")
            return 1
        print(f"target: slot {target.label}, reported {target.name!r} by the "
              f"device" + (f" (ghost {target.ghost!r})" if target.ghost else ""))
        if held:
            print(f"leaving preset {p.slot_label(held[0])} ({held[1]!r}); its "
                  "edit buffer is discarded by the switch")

        # The select decides which preset every insert below edits. A dropped
        # bank or program change would leave the owner's loaded preset in the
        # buffer, and the verification afterwards would still pass because it
        # reads back what it just wrote. Confirm the unit agrees first.
        landed = dev.select_preset(target.number)
        time.sleep(SETTLE)
        if landed is None or landed[0] != target.number:
            print(f"refusing to build: asked for slot {target.label} but the "
                  f"unit reports {landed!r} loaded. Not editing a preset that "
                  "was never checked as empty.")
            return 1
        for col, (eid, label) in enumerate(CHAIN, start=1):
            dev.place_block(ROW, col, eid)
            time.sleep(SETTLE)
            print(f"  placed {label} (eid {eid}) at row {ROW} col {col}")
        for col in range(1, len(CHAIN)):
            dev.connect_cells(ROW, col, ROW)
            time.sleep(SETTLE)
            print(f"  cabled ({ROW},{col}) -> ({ROW},{col + 1})")

        time.sleep(SETTLE)
        cells = sorted(dev.read_grid() or [], key=lambda c: (c.col, c.row))
        print("\ngrid:")
        for line in describe(cells, reg):
            print(line)
        placed = {c.effect_id for c in cells}
        missing = [label for eid, label in CHAIN if eid not in placed]
        blocks = {b.effect_id: b for b in dev.status_dump() or []}
        bypassed = [label for eid, label in CHAIN
                    if eid in blocks and blocks[eid].bypassed]
        # Membership is not a path. A block sitting on the cursor cell at row 1
        # col 1 is "present" and un-starved while being nowhere near the signal
        # - the silent-scene class this project has been bitten by repeatedly.
        # path_audit walks Input to Output over the real cable masks.
        alive, why = scene_alive(cells, blocks, reg)

        print()
        ok = True
        if missing:
            print(f"INCOMPLETE: never landed: {', '.join(missing)}")
            ok = False
        if not alive:
            print(f"NO LIVE SIGNAL PATH: {why}")
            ok = False
        if bypassed:
            print(f"NOTE: bypassed blocks: {', '.join(bypassed)}")
        # On the simulator, say what it did NOT vouch for. Silence here would
        # read as "all verified" when some of it was modelled, not proven.
        undecoded = sorted(getattr(getattr(dev, "sim_core", None),
                                   "undecoded", []) or [])
        if undecoded:
            print("\nsimulated but NOT hardware-verified:")
            for note in undecoded:
                print(f"  !! {note}")

        if ok:
            print(f"live signal path confirmed: {why}")
            print(f"loaded on {target.label}, edit buffer only - nothing "
                  "stored, so the slot still reads <EMPTY> in flash")
            print("PLAY IT. Audible is the claim; your ears outrank every read "
                  "path here.")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
