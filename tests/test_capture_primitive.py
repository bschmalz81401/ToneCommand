"""#162 (K1): the capture-slot primitive on the adapter contract.

Four operations, gated by Capabilities.plays_captures and enforced by
conformance(); the sim implements them in memory (the FM9 has no capture
protocol yet), the real FM9 and the HeadRush adapter answer honestly that
they play no captures.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from fm9 import adapter, protocol as p
from fm9.adapter import (CaptureCapabilities, CaptureInstall, CaptureSlot, CaptureSlots,
                         Capabilities, NO_CAPTURES, conformance)
from fm9.captures import CaptureStore, NoCaptures, get_nam_slots
from fm9.device import FM9
from fm9.nam import read_nam
from fm9.sim import SimFM9
from tests.stub_device import StubDevice


def _record(name="Friedman BE-100 hi", make="Friedman", model="BE-100"):
    import json
    return read_nam(json.dumps({
        "version": "0.5.2", "architecture": "WaveNet", "config": {"layers": []}, "weights": [0.0] * 4,
        "metadata": {"name": name, "gear_make": make, "gear_model": model, "gear_type": "amp", "tone_type": "hi_gain"},
    }))


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_NAM_SLOTS", "0-3")
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "0-511")
    dev = SimFM9()
    dev.status_dump()
    return dev


def test_the_contract_carries_the_gate_the_protocol_and_the_records():
    assert Capabilities().plays_captures is False
    assert ("plays_captures", CaptureSlots) in [(label, proto) for label, _, proto in adapter.CAPABILITY_PROTOCOLS]
    assert CaptureCapabilities()._fields == ("formats", "slots", "whitelist")
    assert CaptureSlot._fields == ("slot", "occupied", "name", "record")
    assert CaptureInstall._fields == ("slot", "verified", "note")
    members = {name for name in dir(CaptureSlots) if not name.startswith("_")}
    assert members == {"capture_capabilities", "list_captures", "install_capture", "remove_capture"}


def test_the_sim_declares_plays_captures_and_conforms(sim):
    assert sim.capabilities().plays_captures is True
    assert conformance(sim) == []
    assert conformance(FM9) == []           # the class, signatures only
    caps = sim.capture_capabilities()
    assert caps == CaptureCapabilities((".nam",), 8, frozenset({0, 1, 2, 3}))


def test_list_install_and_read_back_on_the_sim(sim):
    rows = sim.list_captures()
    assert len(rows) == 8 and all(not row.occupied and row.record is None for row in rows)
    rec = _record()
    got = sim.install_capture(rec, b"\x01\x02\x03", 2)
    assert got == CaptureInstall(2, True, "read back byte for byte")
    row = sim.list_captures()[2]
    assert row.occupied and row.name == "Friedman BE-100 hi" and row.record is rec
    # An occupied, unreferenced slot is overwritten: that is what the whitelist means.
    again = sim.install_capture(_record("Other"), b"\x09", 2)
    assert again.verified and sim.list_captures()[2].name == "Other"


def test_install_refuses_outside_the_whitelist_and_bad_slots(sim):
    with pytest.raises(ValueError, match="slot 5 refused: it is outside TONECOMMAND_NAM_SLOTS"):
        sim.install_capture(_record(), b"\x00", 5)
    with pytest.raises(ValueError, match="slot 8 does not exist"):
        sim.install_capture(_record(), b"\x00", 8)
    with pytest.raises(ValueError, match="does not exist"):
        sim.remove_capture(-1)
    assert all(not row.occupied for row in sim.list_captures())


def test_a_stored_preset_that_names_a_slot_references_it_and_blocks_writes(sim):
    sim.install_capture(_record(), b"\x01", 1)
    # A stored preset carrying capture_slot 1 references the slot.
    sim.sim_core.st.buffer["capture_slot"] = 1
    sim.store_preset(20)
    assert sim.capture_store.references_of(1) == {20}
    with pytest.raises(ValueError, match="install to slot 1 refused: the capture there is used by stored preset 20"):
        sim.install_capture(_record("New"), b"\x02", 1)
    with pytest.raises(ValueError, match="removal from slot 1 refused: it is used by stored preset 20"):
        sim.remove_capture(1)
    # Duplicates collapse; a second preset is named too.
    sim.store_preset(20)
    sim.store_preset(21)
    assert sim.capture_store.references_of(1) == {20, 21}
    with pytest.raises(ValueError, match="preset 20, 21"):
        sim.remove_capture(1)
    # Re-storing preset 20 without the field drops its reference; a malformed value never references.
    sim.sim_core.st.buffer.pop("capture_slot")
    sim.store_preset(20)
    sim.sim_core.st.buffer["capture_slot"] = "one"
    sim.store_preset(21)
    assert sim.capture_store.references_of(1) == set()
    assert "preset 21: capture_slot 'one' is not an integer; no reference recorded" in sim.sim_core.undecoded
    assert sim.remove_capture(1) is True
    assert sim.remove_capture(1) is False     # empty: a no-op
    assert not sim.list_captures()[1].occupied


def test_devices_that_play_no_captures_answer_honestly():
    fm9 = FM9.__new__(FM9)                    # no ports, no store: the real unit today
    assert fm9.capabilities().plays_captures is False
    assert fm9.capture_capabilities() == CaptureCapabilities((), 0, frozenset())
    assert fm9.list_captures() == []
    with pytest.raises(NotImplementedError, match=NO_CAPTURES):
        fm9.install_capture(_record(), b"\x00", 0)
    with pytest.raises(NotImplementedError, match=NO_CAPTURES):
        fm9.remove_capture(0)
    from devices.headrush.adapter import HeadrushAdapter
    assert HeadrushAdapter.CAPABILITIES.plays_captures is False
    assert conformance(HeadrushAdapter) == []
    none = NoCaptures()
    assert none.list_captures() == []
    # The stub device (no capture methods at all) still conforms: the gate is off.
    assert conformance(StubDevice()) == []


def test_declaring_captures_without_the_methods_fails_conformance():
    class Liar(StubDevice):
        def capabilities(self):
            from dataclasses import replace
            return replace(super().capabilities(), plays_captures=True)
    problems = conformance(Liar())
    assert any("plays_captures" in problem and "install_capture" in problem for problem in problems), problems


def test_the_whitelist_parser_matches_the_cab_one(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_NAM_SLOTS", "0-2, 7,x, 1024")
    assert get_nam_slots() == {0, 1, 2, 7}
    monkeypatch.setenv("TONECOMMAND_NAM_SLOTS", "")
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert get_nam_slots() == set()
    store = CaptureStore(slots=2, whitelist={0})
    assert store.capture_capabilities().whitelist == frozenset({0})
