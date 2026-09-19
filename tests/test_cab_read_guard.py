"""Issue #43: the fn 0x19 user-cab read is off at the transport layer.

Measured on hardware 2026-09-05 (firmware 12.x): one fn 0x19 read disconnected
the FM9's MIDI and the unit needed a power cycle. The read was the SAFETY step
of every user-cab install (probe the address before writing), so the install
path inherits the hazard. These tests pin the guard where it has to live: as
the first thing both entry points do, before any frame exists, so no candidate
address path can reach the wire without the operator's flag.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import device as fm9_device
from fm9.sim import SimFM9
from tests.test_cabs import make_cab


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.delenv("TONECOMMAND_ALLOW_CAB_READ", raising=False)
    monkeypatch.setenv("TONECOMMAND_CAB_SLOTS", "0-15")
    dev = SimFM9(server.reg)
    dev.status_dump()
    sent = []
    real = dev.outp.send
    monkeypatch.setattr(dev.outp, "send",
                        lambda msg: (sent.append(msg), real(msg)))
    dev.sent = sent
    return dev


def test_read_user_cab_addr_refuses_before_any_frame(sim):
    with pytest.raises(RuntimeError, match="fn 0x19") as e:
        sim.read_user_cab_addr(0, 0x10)
    assert sim.sent == [], "the guard must fire before a frame is built"
    msg = str(e.value)
    assert "power cycle" in msg and "Cab-Lab" in msg and "#43" in msg
    assert fm9_device.CAB_READ_FLAG in msg


def test_install_paths_do_not_reach_the_read(sim):
    """Since the capture of 2026-09-19 no install consults the guard or
    sends fn 0x19: see tests/test_cab_bank2_install.py. The guard is the
    read path's, and the read path alone."""
    sim.install_user_cab_slot(make_cab(), 1, "U2-x.syx")
    assert 0x19 not in [m.data[4] for m in sim.sent]
    with pytest.raises(RuntimeError, match="fn 0x19"):
        sim.read_user_cab_addr(1, 0x10)


def test_the_flag_opts_in_and_the_read_still_answers(sim, monkeypatch):
    monkeypatch.setenv("TONECOMMAND_ALLOW_CAB_READ", "1")
    res = sim.install_user_cab_slot(make_cab(), 1, "U2-x.syx")
    got = sim.read_user_cab_addr(res.slot, 0x10)
    assert got is not None and sim.sent, "with the flag the read path is intact"


def test_install_cab_route_no_longer_needs_the_flag(sim, monkeypatch):
    import hashlib
    monkeypatch.setattr(server, "_fm9", sim)
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    raw = make_cab()
    h = hashlib.sha1(raw).hexdigest()
    monkeypatch.setattr(server, "_install_cache", {h: raw})
    r = TestClient(server.app).post(
        "/api/install-cab", json={"hash": h, "slot": 1})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert 0x19 not in [m.data[4] for m in sim.sent]
