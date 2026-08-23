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


def resolve_aliases(cells, present: set[int]) -> dict:
    """Map each grid cell position to its true effect id.

    Grid ids alias mod 128 (finding 2), and BOTH the low and high id can
    exist in the same preset (e.g. Amp 1 = 58 and FDBKRET 1 = 186 both
    read 58 on the grid). Constraints used: a real block occupies exactly
    one cell, and a feedback Return has no input cable. So when two cells
    share a raw id and both candidates exist, the cable-less cell past
    column 0 takes the high id.
    """
    raw = {}
    for c in cells:
        if c.effect_id is not None:
            raw.setdefault(c.effect_id, []).append(c)
    out = {}
    for rid, cs in raw.items():
        lo_ok, hi_ok = rid in present, rid + 128 in present
        if len(cs) == 1:
            out[(cs[0].row, cs[0].col)] = rid + 128 if (hi_ok and not lo_ok) else rid
            continue
        starved = [c for c in cs if c.cable_in_mask == 0 and c.col > 0]
        for c in cs:
            high = hi_ok and c in starved
            out[(c.row, c.col)] = rid + 128 if high else rid
    return out


def scene_alive(cells, st, reg) -> tuple[bool, str]:
    present = set(st)
    resolved = resolve_aliases(cells, present)
    by_pos = {}
    for c in cells:
        if c.effect_id is None and not c.is_shunt:
            continue
        eid = resolved.get((c.row, c.col)) if c.effect_id else None
        by_pos[(c.row, c.col)] = (eid, c.is_shunt, c.cable_in_mask)

    def fam(eid):
        got = reg.family_of_effect_id(eid) if eid else None
        return got[0] if got else None

    def passes(eid, is_shunt):
        """Does this hop pass signal in the current scene?"""
        if is_shunt or eid is None:
            return True
        f = fam(eid)
        bk = st.get(eid)
        engaged = bk is not None and not bk.bypassed
        if f == "INPUT":
            return engaged          # no thru on source blocks
        if f == "FDBKRET":
            return engaged          # no grid input; bypass = dead end
        return True                 # engaged or bypassed-with-thru

    starts = [pos for pos, (eid, sh, _) in by_pos.items()
              if fam(eid) == "INPUT" and passes(eid, sh)]
    live, frontier = set(starts), list(starts)
    # the send/return bus: a reachable engaged Send powers every engaged Return
    def bus_jump():
        send_live = any(fam(by_pos[p][0]) == "FDBKSEND" for p in live)
        if not send_live:
            return []
        return [pos for pos, (eid, sh, _) in by_pos.items()
                if pos not in live and fam(eid) == "FDBKRET" and passes(eid, sh)]

    for _ in range(3):              # bus can chain at most a few times
        while frontier:
            r, c = frontier.pop()
            for (nr, nc), (eid, sh, mask) in by_pos.items():
                if (nr, nc) in live or nc != c + 1:
                    continue
                if mask & (1 << (r + 1)) and passes(eid, sh):
                    live.add((nr, nc)); frontier.append((nr, nc))
        jumped = bus_jump()
        if not jumped:
            break
        live.update(jumped); frontier = list(jumped)

    outs = [pos for pos, (eid, sh, _) in by_pos.items() if fam(eid) == "OUTPUT"]
    if not outs:
        return False, "no OUTPUT block on grid"
    for pos in outs:
        eid, sh, _ = by_pos[pos]
        bk = st.get(eid)
        if pos in live and bk is not None and not bk.bypassed:
            return True, "alive"
    if not starts:
        return False, "INPUT bypassed or missing"
    return False, "no live path from INPUT to an engaged OUTPUT"


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
