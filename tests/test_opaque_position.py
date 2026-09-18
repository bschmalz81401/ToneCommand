"""Issue #123: ChainEditing.place_block takes one device-owned position.

The contract used to spell the FM9's geometry (row_1based, col_1based) into
the shared signature, so a rig device with fourteen linear slots could not
implement it without inventing a row. Now `position` is opaque: the FM9
takes a GridPos (or a (row, col) pair) and sends exactly the frames it always
sent; a rig device takes its slot number; conformance() checks the new shape
and rejects the old one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import server
from fm9 import protocol as p
from fm9.adapter import ChainEditing, GridPos, _signature_problems, conformance
from fm9.device import FM9
from fm9.sim import SimFM9
from tests.stub_device import StubDevice


@pytest.fixture
def fm9(monkeypatch):
    dev = SimFM9(server.reg)
    dev.status_dump()
    sent = []
    real = dev.outp.send
    monkeypatch.setattr(dev.outp, "send", lambda msg: (sent.append(list(msg.data)), real(msg)))
    dev.sent = sent
    return dev


def _frames_of(dev):
    return [f for f in dev.sent if len(f) > 4]


def test_fm9_sends_the_same_two_frames_for_a_gridpos_and_a_pair(fm9):
    fm9.place_block(GridPos(2, 5), 94)
    got_pos = _frames_of(fm9)[-2:]
    fm9.sent.clear()
    fm9.place_block((2, 5), 94)
    got_pair = _frames_of(fm9)[-2:]
    want = [p.build_select_grid_cell(2, 5)[1:-1], p.build_set_grid_cell(2, 5, 94)[1:-1]]
    assert got_pos == want, "select cell, then set cell, exactly as before"
    assert got_pair == want


def test_fm9_placement_lands_where_it_always_did(fm9):
    fm9.place_block(GridPos(4, 10), 94)
    grid = {(c.row + 1, c.col + 1): c.effect_id for c in fm9.read_grid() or []}
    assert grid.get((4, 10)) == 94


def test_a_position_that_is_not_a_cell_is_refused_before_any_frame(fm9):
    with pytest.raises(TypeError):
        fm9.place_block(7, 94)                    # a bare slot number is not FM9 geometry
    assert _frames_of(fm9) == []


def test_the_contract_signature_is_position_and_effect_id():
    import inspect
    assert [q for q in inspect.signature(ChainEditing.place_block).parameters if q != "self"] \
        == ["position", "effect_id"]
    assert [q for q in inspect.signature(FM9.place_block).parameters if q != "self"] \
        == ["position", "effect_id"]


def test_conformance_rejects_the_old_row_col_signature():
    class OldShape:
        def place_block(self, row_1based: int, col_1based: int, effect_id: int): ...
        def reorder_block(self, moving_eid: int, ref_eid: int): ...

    class NewShape:
        def place_block(self, position, effect_id: int): ...
        def reorder_block(self, moving_eid: int, ref_eid: int): ...

    assert any("place_block" in q for q in _signature_problems(OldShape, ChainEditing, "ChainEditing."))
    assert _signature_problems(NewShape, ChainEditing, "ChainEditing.") == []


def test_the_fm9_and_the_stub_both_conform():
    assert conformance(FM9) == []
    assert conformance(StubDevice()) == []


def test_a_selected_topology_device_places_by_slot_with_no_row():
    stub = StubDevice()
    assert stub.place_block(3, 19) == 3
    assert stub._chain[3] == 19
    # GridPos is the FM9's business, not the contract's: the stub never sees one
    assert "row" not in StubDevice.place_block.__code__.co_varnames
