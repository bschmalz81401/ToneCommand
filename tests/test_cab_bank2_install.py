"""Issue #43: a Bank 2+ user-cab install goes out exactly as FM9-Edit's
captured write, with no read of any kind, and says it was not read back.

Pins, on the simulator: the frames sent for Bank 2 slot 11 (head `0A 14
00 10`, masked chunks, the file's own tail), that no fn 0x19 frame is ever
built, that the fn 0x19 guard is not consulted (no opt-in flag set), the
TONECOMMAND_CAB_SLOTS refusal for a flat index not listed, that the send
stops at the first frame the unit does not ack, and the route answering
200 with verified false while bank 1 keeps its 409 under the guard.
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
from fm9.sim import SimFM9
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


def test_bank2_install_sends_the_captured_frames_and_nothing_else(sim):
    raw = make_cab(model=0x10)          # an Axe-Fx III export, as artists ship
    res = sim.install_user_cab_at(raw, 2, 11, "BT_x__bank2_num11.syx")
    assert isinstance(res, fm9_device.CabInstall)
    assert (res.idx, res.tag) == (522, 0x10)
    assert res.verified is False and res.acks == 10
    assert "not read back" in res.note and "fn 0x19" in res.note
    frames = [[0xF0, *m.data, 0xF7] for m in sim.sent]
    assert [f[5] for f in frames] == [0x7A] + [0x7B] * 8 + [0x7C]
    assert 0x19 not in _fns(sim)
    # the head, byte for byte as captured for U1.0523: model FM9, flat 522
    # low septet first with the 0x10 flag, tag 0x10
    assert frames[0][4:10] == [0x12, 0x7A, 0x0A, 0x14, 0x00, 0x10]
    # every frame is what retarget builds, re-checksummed on the FM9 model
    assert frames == cabfile.retarget(cabfile.parse(raw), 522)
    assert all(f[4] == 0x12 for f in frames)
    # the simulator filed it under the flat index
    assert (522, 0x10) in sim.outp.core.user_cabs


def test_bank2_never_touches_the_guard_or_a_probe(sim, monkeypatch):
    called = []
    monkeypatch.setattr(fm9_device, "cab_read_guard",
                        lambda: called.append("guard"))
    monkeypatch.setattr(sim, "probe_cab_encoding",
                        lambda b, n: called.append("probe"))
    monkeypatch.setattr(sim, "read_user_cab_addr",
                        lambda *a, **k: called.append("read"))
    sim.install_user_cab_at(make_cab(), 2, 12, "x.syx")
    assert called == []
    assert 0x19 not in _fns(sim)


def test_bank2_slots_are_whitelisted_by_flat_index(sim, monkeypatch):
    # 522 and 523 (Bank 2 slots 11 and 12) are listed by the fixture; 13
    # (flat 524) is not, and neither is anything with the list unset
    with pytest.raises(PermissionError, match="flat index 524"):
        sim.install_user_cab_at(make_cab(), 2, 13, "x.syx")
    assert sim.sent == []
    monkeypatch.setenv("TONECOMMAND_CAB_SLOTS", "0-511")
    with pytest.raises(PermissionError, match="flat index 522"):
        sim.install_user_cab_at(make_cab(), 2, 11, "x.syx")
    # the parser accepts the whole flat list, so a second bank IS listable
    monkeypatch.setenv("TONECOMMAND_CAB_SLOTS", "0-1023")
    assert 1023 in fm9_device.get_cab_slots()
    with pytest.raises(ValueError, match="1-based"):
        sim.install_user_cab_at(make_cab(), 2, 0, "x.syx")
    # nothing listed at all (this checkout's .env would otherwise fill in)
    monkeypatch.setattr(fm9_device, "get_cab_slots", lambda: set())
    with pytest.raises(PermissionError, match="disabled"):
        sim.install_user_cab_at(make_cab(), 2, 11, "x.syx")
    assert sim.sent == []


def test_the_file_is_still_validated_at_the_boundary(sim):
    with pytest.raises(cabfile.CabFileError):
        sim.install_user_cab_at(b"\xf0\x00\x01\x74\x12\x7a\xf7", 2, 11, "x")
    assert sim.sent == []


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
        sim.install_user_cab_at(make_cab(), 2, 11, "x.syx")
    assert [m.data[4] for m in sim.sent] == [0x7A, 0x7B, 0x7B, 0x7B]


def test_bank1_is_unchanged_under_the_guard(sim):
    with pytest.raises(RuntimeError, match="fn 0x19"):
        sim.install_user_cab_at(make_cab(), 1, 1, "U1-x.syx")
    assert sim.sent == []


@pytest.fixture
def client(sim, monkeypatch):
    monkeypatch.setattr(server, "_fm9", sim)
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    raw = make_cab()
    h = hashlib.sha1(raw).hexdigest()
    monkeypatch.setattr(server, "_install_cache", {h: raw})
    return TestClient(server.app), h


def test_install_cab_route_bank2_is_200_verified_false(client, sim):
    c, h = client
    r = c.post("/api/install-cab",
               json={"hash": h, "bank": 2, "number": 11,
                     "filename": "BT_x__bank2_num11.syx"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["verified"] is False
    assert d["bank"] == 2 and d["number"] == 11
    assert "not read back" in d["detail"] and "FM9-Edit" in d["detail"]
    assert 0x19 not in _fns(sim) and len(sim.sent) == 10


def test_install_cab_route_bank1_keeps_its_409(client, sim):
    c, h = client
    r = c.post("/api/install-cab", json={"hash": h, "bank": 1, "number": 1})
    assert r.status_code == 409, r.text
    assert "Cab-Lab" in r.json()["error"]
    assert sim.sent == []


def test_install_cab_route_bank2_outside_the_list_is_403(client, sim):
    c, h = client
    r = c.post("/api/install-cab", json={"hash": h, "bank": 2, "number": 13})
    assert r.status_code == 403, r.text
    assert "flat index 524" in r.json()["error"]
    assert sim.sent == []
