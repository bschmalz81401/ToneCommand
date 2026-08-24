"""Splicing a block into a packed row (issue #10).

Nothing can be inserted between two adjacent blocks by cable alone, because
cables only ever run to the next column. The splice displaces neighbours
right, which is safe because a cleared block keeps its parameters, and
re-cables the span, which is possible because removal and same-row draws are
both decoded. Hardware-verified on fw 12.00; these pin the behaviour.
"""
from fm9.registry import Registry
from fm9.sim import SimFM9

ROW = 2          # the simulator's default preset runs along display row 2
GEQ, VOLUME = 50, 102


def dev():
    return SimFM9(Registry())


def families(d, row=ROW):
    reg = d.registry if hasattr(d, "registry") else Registry()
    out = []
    for c in sorted((c for c in (d.read_grid() or []) if c.row == row - 1),
                    key=lambda c: c.col):
        if c.effect_id:
            fam = reg.family_of_effect_id(c.effect_id)
            out.append((c.col + 1, fam[0] if fam else c.effect_id))
    return out


def test_a_block_lands_where_asked_and_neighbours_move_right():
    with dev() as d:
        d.select_preset(0)
        before = dict(families(d))
        was_at_3 = before.get(3)
        r = d.splice_block(ROW, 3, GEQ)
        assert r["ok"], r["detail"]
        after = dict(families(d))
        assert after[3] == "GEQ", "the new block takes the requested column"
        assert after[4] == was_at_3, "the displaced block moved one column right"


def test_the_chain_stays_continuous():
    with dev() as d:
        d.select_preset(0)
        d.splice_block(ROW, 3, GEQ)
        cells = sorted((c for c in (d.read_grid() or []) if c.row == ROW - 1),
                       key=lambda c: c.col)
        first = cells[0].col
        starved = [c.col + 1 for c in cells if c.col > first and c.cable_in_mask == 0]
        assert starved == [], f"cells with no input cable: {starved}"


def test_displaced_blocks_keep_their_parameters():
    """Hardware: all 588 values across four channels were byte-identical."""
    with dev() as d:
        d.select_preset(0)
        amp = 58
        d.status_dump()
        before = d.bulk_read(amp)
        d.splice_block(ROW, 3, GEQ)
        d.status_dump()
        assert d.bulk_read(amp) == before


def test_it_refuses_when_the_row_has_no_slack():
    """Rather than pushing a block off the end of the grid. The last column
    is the sharpest case: there is nowhere to the right at all."""
    with dev() as d:
        d.select_preset(0)
        d.place_block(ROW, 14, VOLUME)
        assert any(c.col == 13 and c.row == ROW - 1 for c in (d.read_grid() or [])), \
            "the last column must be occupied for this to test what it claims"
        r = d.splice_block(ROW, 14, GEQ)
        assert r["ok"] is False
        assert "off the end of the grid" in r["detail"]


def test_it_refuses_a_column_that_is_already_free():
    with dev() as d:
        d.select_preset(0)
        cols = [c for c, _ in families(d)]
        free = max(cols) + 2
        r = d.splice_block(ROW, free, GEQ)
        assert r["ok"] is False
        assert "already free" in r["detail"]


def test_it_refuses_when_the_span_is_fed_from_another_row():
    """Same-row redraw would silently break routing this code does not model."""
    with dev() as d:
        d.select_preset(0)
        d.place_block(ROW + 1, 3, VOLUME)
        d.connect_cells(ROW + 1, 3, ROW)          # cross-row feed into the span
        r = d.splice_block(ROW, 3, GEQ)
        assert r["ok"] is False
        assert "another row" in r["detail"]


def test_the_report_says_what_it_moved_and_what_it_spent():
    with dev() as d:
        d.select_preset(0)
        r = d.splice_block(ROW, 3, GEQ)
        assert r["placed_at"] == (ROW, 3)
        assert all(dst == src + 1 for _, src, dst in r["moved"])
        assert isinstance(r["spent_a_shunt"], bool)
