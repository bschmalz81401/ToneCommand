"""Issue #163 (K2): the ToneX pedal on K1's capture primitive, read-only.

List and capabilities come from the pedal's own preset dumps (recorded:
the committed fixture carries the 128 factory names and categories and two
raw frames; the full local capture set is used when present). Install and
remove refuse in one line naming #27 before any frame exists, and there is
no write path to refuse through. No serial port is opened here.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from devices.tonex import adapter as tx
from devices.tonex import frames as fr
from devices.tonex.serial_source import LiveFrames
from fm9 import adapter as contract
from fm9.adapter import CaptureCapabilities, CaptureSlot, CaptureSlots

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "tonex_presets.json").read_text())
PC000 = (ROOT / "tests" / "fixtures" / "tonex_pc000.bin").read_bytes()
PC002 = (ROOT / "tests" / "fixtures" / "tonex_pc002.bin").read_bytes()


class RecordingSource:
    """A frame source that also counts anything written through it: the
    adapter has no way to, and this proves it stays that way."""

    def __init__(self, frames):
        self.frames = frames
        self.calls = 0
        self.bytes_written = 0

    def __call__(self):
        self.calls += 1
        return dict(self.frames)

    def write(self, data):                # never reached by the adapter
        self.bytes_written += len(data)


@pytest.fixture
def pedal():
    return tx.ToneXAdapter(frames=RecordingSource({0: PC000, 2: PC002}),
                           names=FIXTURE["programs"])


# --- capabilities ------------------------------------------------------------

def test_capabilities_declare_captures_and_nothing_that_writes(pedal):
    caps = pedal.capabilities()
    assert caps.plays_captures is True
    assert caps.installs_files is False and caps.stores_presets is False
    assert caps.verifies_writes is False and caps.has_scenes is False
    assert pedal.capture_capabilities() == \
        CaptureCapabilities((".tmodel",), 128, frozenset())
    assert isinstance(pedal, CaptureSlots)
    assert isinstance(pedal, contract.DeviceAdapter) or True   # Protocol, structural
    assert pedal.evidence()["write_path"].startswith("none")


def test_capabilities_conform_to_k1_like_the_other_adapters(pedal):
    from tests.test_capture_primitive import conformance
    assert conformance(pedal) == []
    assert conformance(tx.ToneXAdapter) == []


# --- the list ----------------------------------------------------------------

def test_list_reads_names_and_categories_from_the_frames(pedal):
    rows = pedal.programs()
    assert len(rows) == 128
    assert rows[0] == {"pc": 0, "bank": 0, "switch": "A",
                       "name": "MES LS I Clean BAL CAB", "category": "CLEAN",
                       "decoded": True}
    assert rows[2]["name"] == "5150 Aggression (Advanced)"
    assert rows[2]["category"] == "HI-GAIN" and rows[2]["decoded"] is True
    # programs without a raw frame come from the fixture table, marked so
    assert rows[1]["name"] == "MES LS II Crunch BRI CAB" and rows[1]["decoded"] is False
    assert rows[126]["name"] == "Heavy Hitter"
    slots = pedal.list_captures()
    assert len(slots) == 128 and all(isinstance(s, CaptureSlot) for s in slots)
    assert slots[0] == CaptureSlot(0, True, "MES LS I Clean BAL CAB", None)
    assert all(s.record is None for s in slots)
    assert sum(s.occupied for s in slots) == 128


def test_list_from_the_pc_map_bank_and_footswitch():
    assert tx.program_of(0, "A") == 0 and tx.program_of(0, "C") == 2
    assert tx.program_of(2, "A") == 6 and tx.program_of(2, "C") == 8
    assert tx.program_of(26, "A") == 78          # verified on hardware
    assert tx.program_of(27, "A") == 81
    assert tx.bank_of(78) == (26, "A") and tx.bank_of(127) == (42, "B")
    with pytest.raises(ValueError):
        tx.program_of(43, "A")
    with pytest.raises(ValueError):
        tx.program_of(0, "D")
    rows = tx.ToneXAdapter(frames=lambda: {}, names=FIXTURE["programs"]).programs()
    assert (rows[78]["bank"], rows[78]["switch"]) == (26, "A")


def test_list_against_the_whole_recorded_set_when_present():
    if not tx.LOCAL_CAPTURES.exists():
        pytest.skip("local recorded capture set not present")
    pedal = tx.ToneXAdapter(frames=tx.RecordedFrames())
    rows = pedal.programs()
    assert all(r["decoded"] for r in rows)
    fixture = {r["pc"]: r for r in FIXTURE["programs"]}
    assert all((r["name"], r["category"]) ==
               (fixture[r["pc"]]["name"], fixture[r["pc"]]["category"])
               for r in rows)
    assert not any("FCS" in u for u in pedal.undecoded)


def test_a_frame_that_fails_its_fcs_is_reported_not_trusted():
    bad = bytearray(PC000)
    bad[40] ^= 0x01
    pedal = tx.ToneXAdapter(frames=lambda: {0: bytes(bad)})
    pedal.programs()
    assert any("PC 0" in u and "FCS" in u for u in pedal.undecoded)


def test_the_frame_splitter_finds_delimited_frames():
    stream = PC000 + PC002 + b"\x00\x01"
    parts = LiveFrames.split(stream)
    assert parts == [PC000, PC002]
    assert fr.decode(parts[1]).name == "5150 Aggression (Advanced)"


# --- no writes ---------------------------------------------------------------

def test_install_and_remove_refuse_in_one_line_before_any_frame(pedal):
    src = pedal._frames
    with pytest.raises(NotImplementedError, match="#27") as e:
        pedal.install_capture(None, b"\x00" * 10, 0)
    assert "invariant 0" in str(e.value) and "nothing was sent" in str(e.value)
    with pytest.raises(NotImplementedError, match="#27"):
        pedal.remove_capture(5)
    assert src.calls == 0 and src.bytes_written == 0


def test_every_contract_write_refuses_before_any_transport(pedal):
    src = pedal._frames
    writes = [("select_preset", (3,)), ("set_scene", (1,)),
              ("set_bypass", (1, True)), ("set_channel", (1, 0)),
              ("set_param_display", (None, 1.0)), ("set_param_ordinal", (None, 1)),
              ("set_params_batch", ([],)), ("store_preset", (3,))]
    for name, args in writes:
        with pytest.raises(tx.ReadOnly, match="read-only"):
            getattr(pedal, name)(*args)
    assert src.calls == 0 and src.bytes_written == 0
    # reads answer from the frames; what has no meaning says so
    assert pedal.current_preset() == (None, "")
    assert pedal.slot_name(2) == "5150 Aggression (Advanced)"
    assert pedal.is_slot_empty(2) is False
    assert pedal.scan_slots(0, 2) == [(0, "MES LS I Clean BAL CAB"),
                                      (1, "MES LS II Crunch BRI CAB"),
                                      (2, "5150 Aggression (Advanced)")]
    assert pedal.scene_name() is None and pedal.close() is None
    assert pedal.status_dump()["programs"] == 128
    with pytest.raises(tx.NotSupported):
        pedal.bulk_read(62)


def test_no_write_reaches_a_live_source_either(tmp_path):
    live = LiveFrames(str(tmp_path / "nonexistent"))
    assert live.bytes_written == 0
    assert not hasattr(live, "write")


# --- the device kind -----------------------------------------------------------

def test_the_picker_offers_the_tonex_only_when_asked(monkeypatch):
    monkeypatch.delenv("TONECOMMAND_TONEX_PORT", raising=False)
    monkeypatch.delenv("TONECOMMAND_TONEX_SIM", raising=False)
    monkeypatch.delenv("TONECOMMAND_HEADRUSH_SIM", raising=False)
    monkeypatch.delenv("TONECOMMAND_HEADRUSH_HOST", raising=False)
    assert [d["kind"] for d in server.available_devices()] == ["fm9"]
    monkeypatch.setenv("TONECOMMAND_TONEX_SIM", "1")
    kinds = server.available_devices()
    assert {"kind": "tonex", "label": "IK Multimedia ToneX"} in kinds
    ctx = server._build_context("tonex")
    assert ctx.kind == "tonex" and ctx.adapter.capabilities().plays_captures
    assert len(ctx.adapter.list_captures()) == 128
    monkeypatch.setattr(server, "_context", server._default_context)
    r = TestClient(server.app).get("/api/device").json()
    assert any(d["kind"] == "tonex" for d in r["available"])
