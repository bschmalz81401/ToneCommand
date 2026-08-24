"""Cable removal: the routing message with op 0x02.

Hardware-verified on fw 12.00 (PROTOCOL.md finding 24). The simulator had
modelled this behaviour all along; these tests pin the semantics that the
splice in issue #10 depends on, above all that removal is SELECTIVE.
"""
from fm9 import protocol as p
from fm9.registry import Registry
from fm9.sim import SimFM9

ROW = 3


def chain(dev):
    """INPUT -> amp -> cab -> OUTPUT on row 3, fully cabled."""
    dev.select_preset(386)
    for col, eid in ((1, 37), (2, 58), (3, 62), (4, 42)):
        dev.place_block(ROW, col, eid)
    for col in (1, 2, 3):
        dev.connect_cells(ROW, col, ROW)


def masks(dev, row=ROW):
    return {c.col + 1: c.cable_in_mask for c in (dev.read_grid() or [])
            if c.row == row - 1}


def test_the_op_bytes_are_distinct():
    assert p.ROUTING_CONNECT == 0x01 and p.ROUTING_DISCONNECT == 0x02


def test_the_two_ops_differ_only_in_the_op_byte():
    """Same geometry encoding, which is why removal needed no new decoding."""
    on = p.build_set_grid_routing(3, 2, 3, p.ROUTING_CONNECT)
    off = p.build_set_grid_routing(3, 2, 3, p.ROUTING_DISCONNECT)
    differing = [i for i, (a, b) in enumerate(zip(on, off)) if a != b]
    assert len(differing) == 2, "only the op byte and the checksum may differ"


def test_removal_clears_the_destination_mask():
    dev = SimFM9(Registry())
    with dev:
        chain(dev)
        assert masks(dev)[3] != 0
        dev.connect_cells(ROW, 2, ROW, disconnect=True)
        assert masks(dev)[3] == 0


def test_removal_is_idempotent_not_a_toggle():
    """Draw is idempotent (finding 6); removal has to be too, or a retry
    after a dropped reply would put the cable back."""
    dev = SimFM9(Registry())
    with dev:
        chain(dev)
        for _ in range(3):
            dev.connect_cells(ROW, 2, ROW, disconnect=True)
        assert masks(dev)[3] == 0


def test_remove_and_redraw_round_trips():
    dev = SimFM9(Registry())
    with dev:
        chain(dev)
        before = masks(dev)[3]
        for _ in range(3):
            dev.connect_cells(ROW, 2, ROW, disconnect=True)
            assert masks(dev)[3] == 0
            dev.connect_cells(ROW, 2, ROW)
            assert masks(dev)[3] == before


def test_removal_is_selective_on_a_multi_source_cell():
    """The property the #10 splice rests on: cut the old feeder without
    cutting the new one. Hardware: 0b11000 -> 0b1000."""
    dev = SimFM9(Registry())
    with dev:
        chain(dev)
        dev.place_block(4, 2, 102)              # a VOLUME on row 4
        dev.connect_cells(4, 2, ROW)            # second feed into the cab
        both = masks(dev)[3]
        assert bin(both).count("1") == 2, f"expected two source bits, got {bin(both)}"
        dev.connect_cells(4, 2, ROW, disconnect=True)
        one = masks(dev)[3]
        assert bin(one).count("1") == 1
        assert one & (1 << ROW), "the surviving bit must be the row-3 feeder"


def test_verified_same_row_geometry_is_not_reported_as_undecoded():
    """Rows 2-5 same-row are hardware-confirmed; the simulator used to flag
    rows 3 and 4 as unverified, which would send someone chasing a
    non-problem."""
    dev = SimFM9(Registry())
    with dev:
        dev.select_preset(386)
        for row in (2, 3, 4, 5):
            dev.place_block(row, 1, 102 + row)
            dev.connect_cells(row, 1, row)
        cable_notes = [u for u in dev.sim_core.undecoded if "cable" in u]
        assert cable_notes == [], f"unexpected undecoded report: {cable_notes}"
