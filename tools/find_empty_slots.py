#!/usr/bin/env python3
"""Find the empty preset slots on the unit, so a from-scratch build has a
safe target.

    python tools/find_empty_slots.py                 # all 512 slots
    python tools/find_empty_slots.py 386 444          # just a range
    TONECOMMAND_SIM=1 python tools/find_empty_slots.py

NON-DESTRUCTIVE, unlike every other read tool here: fn 0x0D answers by slot
number straight out of flash, so nothing is selected, the edit buffer is
not discarded, and the preset you are playing keeps playing. Safe to run
mid-session.

Slot numbers are reported both ways: the WIRE number this tool and the MIDI
protocol use (0-511) and the number FM9-Edit and the front panel show for the
same slot (1-512). They differ by one, and confusing them is how the wrong
preset gets cleared.

A slot is empty when the FM9 names it "<EMPTY>" - the device's own marker,
not a guess. Cleared slots usually carry a ghost: the tail of the name that
used to be there, left in the name field. Ghosts are reported because they
tell you what a slot used to hold, but they are never a current name.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9 import protocol as p  # noqa: E402
from fm9.registry import Registry  # noqa: E402


def runs(nums: list[int]) -> list[tuple[int, int]]:
    """Collapse sorted slot numbers into contiguous (first, last) ranges."""
    out: list[list[int]] = []
    for n in sorted(nums):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return [(a, b) for a, b in out]


def main(start: int, end: int) -> int:
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    with dev:
        held = dev.current_preset()
        found = list(dev.scan_slots(start, end))
        empty = [s for s in found if s.empty]
        silent = (end - start + 1) - len(found)

        print(f"scanned {len(found)} of {end - start + 1} slots "
              f"({start}-{end}), no select performed")
        if silent:
            print(f"  {silent} slot(s) did not answer and were skipped")
        print(f"  EMPTY    {len(empty)}")
        print(f"  OCCUPIED {len(found) - len(empty)}")

        print("\nempty ranges (wire, and as FM9-Edit shows them):")
        for a, b in runs([s.number for s in empty]) or []:
            span = f"{b - a + 1} slots" if b > a else "1 slot"
            ed = (f"FM9-Edit {p.editor_number(a)}-{p.editor_number(b)}"
                  if b > a else f"FM9-Edit {p.editor_number(a)}")
            print(f"  {a}-{b}  ({ed}, {span})" if b > a
                  else f"  {a}  ({ed}, {span})")
        if not empty:
            print("  none - every slot in this range holds a preset")

        ghosts = [s for s in empty if s.ghost]
        if ghosts:
            print(f"\n{len(ghosts)} empty slot(s) carry a ghost of an old name:")
            for s in ghosts[:20]:
                print(f"  {s.label}: {s.ghost!r}")
            if len(ghosts) > 20:
                print(f"  ... and {len(ghosts) - 20} more")

        if empty:
            widest = max(runs([s.number for s in empty]),
                         key=lambda r: r[1] - r[0])
            print(f"\nsuggested from-scratch target: {empty[0].label} "
                  f"(widest free run {widest[0]}-{widest[1]}, FM9-Edit "
                  f"{p.editor_number(widest[0])}-{p.editor_number(widest[1])})")
        after = dev.current_preset()
        print(f"loaded preset: {held} -> {after}"
              f"{'  UNCHANGED' if held == after else '  DISTURBED'}")
    return 0 if empty else 1


if __name__ == "__main__":
    a = sys.argv[1:]
    sys.exit(main(int(a[0]) if a else 0, int(a[1]) if len(a) > 1 else 511))
