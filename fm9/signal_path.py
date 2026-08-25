"""Walking the routing grid to decide whether signal actually gets through.

Lifted out of tools/path_audit.py so the library can use it too: the device
layer needs this check, and tools/ is not a shipped package (pyproject
installs fm9 and server), so importing it from fm9 would break an installed
copy. path_audit.py keeps its public names by importing them back.

The distinction this draws is the important one. "The write landed" and "the
preset makes sound" are different claims, and five separate silent-preset
classes have each passed write-level verification: a severed Return, a
severed cable, a silently refused insert, bypassed Input blocks, and a block
present on the grid but stranded off the path.
"""
from __future__ import annotations


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
    """Is there a live path from INPUT to an engaged OUTPUT in this scene?

    Thin wrapper over walk(), kept because it is the shape every existing
    caller uses and because a verdict is what an audit wants.
    """
    w = walk(cells, st, reg)
    return w["alive"], w["why"]


def walk(cells, st, reg) -> dict:
    """The same traversal, with its working shown.

    Returns the verdict AND the set of cells the signal actually reaches, so
    the UI can light the live path rather than merely being told a scene is
    alive. Extracted from scene_alive on 2026-08-29; the traversal itself is
    unchanged, which matters because five silent-scene classes were found the
    hard way to get it right.
    """
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

    def done(alive, why):
        return {"alive": alive, "why": why, "live": live,
                "resolved": resolved, "by_pos": by_pos}

    outs = [pos for pos, (eid, sh, _) in by_pos.items() if fam(eid) == "OUTPUT"]
    if not outs:
        return done(False, "no OUTPUT block on grid")
    for pos in outs:
        eid, sh, _ = by_pos[pos]
        bk = st.get(eid)
        if pos in live and bk is not None and not bk.bypassed:
            return done(True, "alive")
    if not starts:
        return done(False, "INPUT bypassed or missing")
    return done(False, "no live path from INPUT to an engaged OUTPUT")
