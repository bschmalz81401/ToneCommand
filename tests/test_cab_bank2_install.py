"""Issue #43: one user-cab install path for the FM9's single flat user list,
sent as FM9-Edit's captured write and verified by the unit's own per-slot
name read.

Pins, on the simulator: the frames sent for a Bundle-Map (Bank 2, Number
11) install go to flat slot 11 with the captured head (`0B 10 00 10`), the
masked chunks and the file's own tail; no fn 0x19 frame is ever built and
the fn 0x19 guard is not consulted (no opt-in flag set); the whitelist is
by slot and its refusal names what the slot holds; the send stops at the
first frame the unit does not ack; the name read-back drives `verified`
(a match with the expected name, or a cab where there was none) and
`ok` (a cab is there); a bank other than 2 is refused by name; the route
answers 200 with both flags and the names, 403 outside the whitelist.
"""
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import cabfile
from fm9 import device as fm9_device
from fm9 import protocol as p
from fm9.sim import SimFM9, SimFM9Core
from tests.test_cabs import make_cab


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.delenv("TONECOMMAND_ALLOW_CAB_READ", raising=False)
    monkeypatch.setenv("TONECOMMAND_CAB_SLOTS", "0-15,522-523")
    dev = SimFM9(server.reg)
    dev.status_dump()
    sent = []
    real = dev.outp.send
    monkeypatch.setattr(dev.outp, "send",
                        lambda msg: (sent.append(msg), real(msg)))
    dev.sent = sent
    return dev


def _fns(sim):
    return [m.data[4] for m in sim.sent if m.type == "sysex"]


def _cab_frames(sim):
    return [[0xF0, *m.data, 0xF7] for m in sim.sent
            if m.data[4] in cabfile.CAB_DUMP_FNS]


def _sim_name(raw):
    """The name the simulator derives for this file once installed: from
    the chunk payloads as sent (masked, re-checksummed), like the unit
    would derive it from what it received."""
    frames = cabfile.retarget(cabfile.parse(raw), 0)
    return SimFM9Core.cab_name_for([f[6:-2] for f in frames[1:-1]])


def test_bundle_map_bank2_number11_is_flat_slot_11(sim):
    raw = make_cab(model=0x10)          # an Axe-Fx III export, as artists ship
    res = sim.install_user_cab_at(raw, 2, 11, "cabs/BT_x.syx")
    assert isinstance(res, fm9_device.CabInstall)
    assert res.slot == 11 and res.editor == "U1.0012"
    frames = _cab_frames(sim)
    assert [f[5] for f in frames] == [0x7A] + [0x7B] * 8 + [0x7C]
    assert 0x19 not in _fns(sim)
    # the head as captured for the layout: model FM9, slot 11 low septet
    # first with the 0x10 flag, then 00 and the 0x10 tag
    assert frames[0][4:10] == [0x12, 0x7A, 0x0B, 0x10, 0x00, 0x10]
    assert frames == cabfile.retarget(cabfile.parse(raw), 11)
    assert all(f[4] == 0x12 for f in frames)
    assert res.acks == 10
    assert (11, 0x10) in sim.outp.core.user_cabs


def test_the_captured_slot_522_goes_out_as_captured(sim):
    res = sim.install_user_cab_slot(make_cab(), 522, "x.syx")
    assert _cab_frames(sim)[0][6:10] == [0x0A, 0x14, 0x00, 0x10]
    assert res.slot == 522 and res.editor == "U1.0523"


def test_verified_means_the_unit_read_the_name_back(sim):
    raw = make_cab()
    assert sim.read_user_cab_name(522) == p.EMPTY_SLOT_NAME
    res = sim.install_user_cab_slot(raw, 522, "x.syx")
    assert res.landed and res.verified
    assert res.name_before == p.EMPTY_SLOT_NAME
    assert res.name_after == _sim_name(raw)
    assert "verified by the unit" in res.note and "U1.0523" in res.note
    # the name read is fn 0x01 sub 0x4B with the head's slot bytes
    reads = [m for m in sim.sent if m.data[4] == 0x01 and m.data[5] == 0x4B]
    assert len(reads) == 3                      # the probe above, before, after
    assert list(reads[1].data[11:13]) == [0x0A, 0x14]
    # with the name the bundle map expects: a match verifies, else not
    ok = sim.install_user_cab_slot(raw, 523, "x.syx", expect_name=_sim_name(raw))
    assert ok.verified
    no = sim.install_user_cab_slot(raw, 523, "x.syx", expect_name="BT_Cab_01")
    assert no.landed and not no.verified
    assert "expected 'BT_Cab_01'" in no.note and "reads back differently" in no.note


def test_install_never_touches_the_guard_or_fn_0x19(sim, monkeypatch):
    called = []
    monkeypatch.setattr(fm9_device, "cab_read_guard",
                        lambda: called.append("guard"))
    monkeypatch.setattr(sim, "read_user_cab_addr",
                        lambda *a, **k: called.append("read"))
    sim.install_user_cab_at(make_cab(), 2, 12, "x.syx")
    sim.install_user_cab_slot(make_cab(), 1, "x.syx")
    assert called == []
    assert 0x19 not in _fns(sim)


def test_whitelist_is_by_slot_and_names_what_is_there(sim, monkeypatch):
    # 16 (U1.0017) is not listed by the fixture
    with pytest.raises(PermissionError, match=r"slot 16 \(FM9-Edit U1.0017\)"):
        sim.install_user_cab_at(make_cab(), 2, 16, "x.syx")
    assert _cab_frames(sim) == []
    # a listed slot with a cab in it, then unlisted: the refusal names it
    raw = make_cab()
    sim.install_user_cab_slot(raw, 15, "x.syx")
    monkeypatch.setenv("TONECOMMAND_CAB_SLOTS", "0-14")
    with pytest.raises(PermissionError, match="it holds '%s' now" % _sim_name(raw)):
        sim.install_user_cab_slot(raw, 15, "x.syx")
    with pytest.raises(PermissionError, match="it is empty now"):
        sim.install_user_cab_slot(raw, 522, "x.syx")
    # the parser accepts the whole flat list
    monkeypatch.setenv("TONECOMMAND_CAB_SLOTS", "0-1023")
    assert 1023 in fm9_device.get_cab_slots()
    with pytest.raises(ValueError, match="out of range"):
        sim.install_user_cab_slot(raw, 1024, "x.syx")
    # nothing listed at all (this checkout's .env would otherwise fill in)
    monkeypatch.setattr(fm9_device, "get_cab_slots", lambda: set())
    with pytest.raises(PermissionError, match="disabled"):
        sim.install_user_cab_slot(raw, 522, "x.syx")


def test_a_bank_other_than_user_is_refused_by_name(sim):
    with pytest.raises(ValueError, match="bank 3 is not the FM9's user bank"):
        sim.install_user_cab_at(make_cab(), 3, 2, "x.syx")
    with pytest.raises(ValueError, match="bank 1 is not"):
        sim.install_user_cab_at(make_cab(), 1, 2, "x.syx")
    assert 0x7A not in _fns(sim)


def test_the_file_is_still_validated_at_the_boundary(sim):
    with pytest.raises(cabfile.CabFileError):
        sim.install_user_cab_slot(b"\xf0\x00\x01\x74\x12\x7a\xf7", 522, "x")
    assert _cab_frames(sim) == []


def test_a_missing_ack_stops_the_send_right_there(sim, monkeypatch):
    # the simulator acks like the unit; take the ack for the 3rd chunk away
    core = sim.outp.core
    real = core._fn_7b
    seen = {"n": 0}

    def flaky(b):
        seen["n"] += 1
        out = real(b)
        return [] if seen["n"] == 3 else out
    monkeypatch.setattr(core, "_fn_7b", flaky)
    orig = fm9_device.FM9._await_ack          # same wait, shorter timeout
    monkeypatch.setattr(fm9_device.FM9, "_await_ack",
                        lambda self, fn, timeout=1.0: orig(self, fn, 0.05))
    with pytest.raises(RuntimeError, match="frame 4 of 10"):
        sim.install_user_cab_slot(make_cab(), 522, "x.syx")
    assert [f[5] for f in _cab_frames(sim)] == [0x7A, 0x7B, 0x7B, 0x7B]


def test_the_legacy_wrapper_is_the_same_path(sim):
    cf = sim.install_user_cab(make_cab(), 3, "U4-x.syx")
    assert cf.chunks == 8
    assert _cab_frames(sim)[0][6:8] == [0x03, 0x10]


@pytest.fixture
def client(sim, monkeypatch):
    monkeypatch.setattr(server, "_fm9", sim)
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    raw = make_cab()
    h = hashlib.sha1(raw).hexdigest()
    monkeypatch.setattr(server, "_install_cache", {h: raw})
    return TestClient(server.app), h, raw


def test_install_cab_route_bundle_destination(client, sim):
    c, h, raw = client
    r = c.post("/api/install-cab",
               json={"hash": h, "bank": 2, "number": 11,
                     "filename": "cabs/BT_x.syx", "name": _sim_name(raw)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["verified"] is True
    assert d["slot"] == 11 and d["editor"] == "U1.0012"
    assert d["name_before"] == p.EMPTY_SLOT_NAME
    assert d["name_after"] == _sim_name(raw)
    assert "verified by the unit" in d["detail"]
    assert 0x19 not in _fns(sim)


def test_install_cab_route_name_mismatch_is_ok_but_not_verified(client, sim):
    c, h, raw = client
    r = c.post("/api/install-cab",
               json={"hash": h, "bank": 2, "number": 12, "name": "BT_Cab_01"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["verified"] is False
    assert "expected 'BT_Cab_01'" in d["detail"]


def test_install_cab_route_flat_slot_and_refusals(client, sim):
    c, h, raw = client
    r = c.post("/api/install-cab", json={"hash": h, "slot": 522})
    assert r.status_code == 200 and r.json()["editor"] == "U1.0523"
    r = c.post("/api/install-cab", json={"hash": h, "bank": 2, "number": 16})
    assert r.status_code == 403, r.text
    assert "U1.0017" in r.json()["error"] and "it is empty now" in r.json()["error"]
    r = c.post("/api/install-cab", json={"hash": h, "bank": 3, "number": 2})
    assert r.status_code == 422 and "not the FM9's user bank" in r.json()["error"]
    r = c.post("/api/install-cab", json={"hash": h})
    assert r.status_code == 400
