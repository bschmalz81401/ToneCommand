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


def test_install_user_cab_at_refuses_before_parse_whitelist_or_probe(sim, monkeypatch):
    # Bank 2 is exactly the case #43 is about, and it is OUTSIDE the
    # whitelist here: the guard still fires first, before the whitelist
    # would have answered, and before any of _cab_addr_candidates is tried.
    probed = []
    monkeypatch.setattr(sim, "probe_cab_encoding",
                        lambda b, n: probed.append((b, n)))
    with pytest.raises(RuntimeError, match="fn 0x19"):
        sim.install_user_cab_at(make_cab(), 2, 11, "U1-x.syx")
    with pytest.raises(RuntimeError, match="fn 0x19"):
        sim.install_user_cab_at(b"not even a cab file", 1, 1, "junk.syx")
    assert probed == [] and sim.sent == []


def test_the_flag_opts_in_and_the_sim_still_round_trips(sim, monkeypatch):
    monkeypatch.setenv("TONECOMMAND_ALLOW_CAB_READ", "1")
    cf, idx, tag = sim.install_user_cab_at(make_cab(), 1, 1, "U1-x.syx")
    got = sim.read_user_cab_addr(idx, tag)
    assert got is not None and sim.sent, "with the flag the path is intact"


def test_install_cab_route_answers_409_with_the_supported_route(sim, monkeypatch):
    import hashlib
    monkeypatch.setattr(server, "_fm9", sim)
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    raw = make_cab()
    h = hashlib.sha1(raw).hexdigest()
    monkeypatch.setattr(server, "_install_cache", {h: raw})
    r = TestClient(server.app).post(
        "/api/install-cab", json={"hash": h, "bank": 1, "number": 1})
    assert r.status_code == 409, r.text
    assert "Cab-Lab" in r.json()["error"] and "48 kHz" in r.json()["error"]
    assert sim.sent == []


def test_candidate_addressing_is_unchanged():
    """Bank 2+ addressing stays open on the issue: nothing here guesses."""
    assert list(fm9_device.FM9._cab_addr_candidates(2, 11)) == [
        (10, 0x11), (512 + 10, 0x10)]
