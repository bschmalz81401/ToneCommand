"""Issues #83 and #84: audition a cab in the edit buffer, commit only through
a plan, leave nothing behind.

The audition routes are the same two discrete CABINET writes a plan's set_cab
makes (server._select_cab), so the simulator proves the two things a rig
owner cares about: no store frame and no user-cab frame is ever sent by an
audition, and ending one puts the original cab back, byte for byte on the
user-cab store and value for value on the block.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9.sim import SimFM9

STORE_SUB = 0x26            # fn 0x01 sub 0x26: store the buffer to a slot
CAB_WIRE_FNS = {0x19, 0x7A, 0x7B, 0x7C}


@pytest.fixture
def rig(monkeypatch):
    dev = SimFM9(server.reg)
    dev.status_dump()
    sent = []
    real = dev.outp.send
    monkeypatch.setattr(dev.outp, "send",
                        lambda msg: (sent.append(msg), real(msg)))
    dev.sent = sent
    monkeypatch.setattr(server, "_fm9", dev)
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    monkeypatch.setattr(server, "_audition",
                        {"open": False, "original": None, "current": None,
                         "last_error": None})
    return dev


@pytest.fixture
def client(rig):
    return TestClient(server.app)


def _frames(dev):
    out = []
    for m in dev.sent:
        if m.type != "sysex":
            continue
        d = list(m.data)
        out.append((d[4], d[5] if len(d) > 5 else None))
    return out


def _no_store_no_cab_wire(dev):
    fr = _frames(dev)
    assert not [f for f in fr if f == (0x01, STORE_SUB)], "a store was sent"
    assert not [f for f in fr if f[0] in CAB_WIRE_FNS], "user-cab wire touched"


def test_audition_selects_in_the_edit_buffer_and_reads_back(client, rig):
    before = server._read_cab(rig)
    r = client.post("/api/cab/audition", json={"bank": 3, "ordinal": 40})
    assert r.status_code == 200, r.text
    assert server._read_cab(rig) == (3, 40)
    st = r.json()["audition"]
    assert st["open"] and st["original"]["bank"] == before[0] \
        and st["original"]["ordinal"] == before[1]
    assert st["current"] == {"bank": 3, "ordinal": 40,
                             "label": server.cab_label(3, 40)}
    assert client.get("/api/cab/audition").json() == st
    _no_store_no_cab_wire(rig)


def test_a_second_audition_keeps_the_first_original(client, rig):
    before = server._read_cab(rig)
    client.post("/api/cab/audition", json={"bank": 3, "ordinal": 40})
    client.post("/api/cab/audition", json={"bank": 3, "ordinal": 46})
    st = client.get("/api/cab/audition").json()
    assert (st["original"]["bank"], st["original"]["ordinal"]) == before
    assert (st["current"]["bank"], st["current"]["ordinal"]) == (3, 46)


def test_end_restores_the_original_and_closes(client, rig):
    before = server._read_cab(rig)
    client.post("/api/cab/audition", json={"bank": 3, "ordinal": 40})
    r = client.post("/api/cab/audition/end")
    assert r.status_code == 200 and r.json()["ok"]
    assert server._read_cab(rig) == before
    assert client.get("/api/cab/audition").json()["open"] is False
    _no_store_no_cab_wire(rig)


def test_end_without_an_audition_is_a_no_op(client, rig):
    n = len(rig.sent)
    r = client.post("/api/cab/audition/end")
    assert r.status_code == 200 and "no audition" in r.json()["detail"]
    assert len(rig.sent) == n


def test_gig_lock_refuses_both_routes(client, rig, monkeypatch):
    monkeypatch.setattr(server, "_gig_mode", {"on": True})
    assert client.post("/api/cab/audition", json={"ordinal": 1}).status_code == 423
    assert client.post("/api/cab/audition/end").status_code == 423
    assert rig.sent == []


def test_bad_input_is_400(client, rig):
    assert client.post("/api/cab/audition", json={}).status_code == 400
    assert client.post("/api/cab/audition", json={"ordinal": "x"}).status_code == 400
    assert rig.sent == []


def test_start_is_transactional_on_a_bad_read_back(client, rig, monkeypatch):
    """Design review F1.1: a write that does not land restores the original,
    clears the session and answers 502, still with no store or cab frame."""
    before = server._read_cab(rig)
    real = server._select_cab

    def flaky(fm9, bank, ordinal, instance=1):
        if (bank, ordinal) == (3, 46):
            real(fm9, 3, 40, instance)          # lands somewhere else
            return False, before, (3, 40)
        return real(fm9, bank, ordinal, instance)

    monkeypatch.setattr(server, "_select_cab", flaky)
    r = client.post("/api/cab/audition", json={"bank": 3, "ordinal": 46})
    assert r.status_code == 502, r.text
    assert r.json()["restored"] is True
    assert server._read_cab(rig) == before
    assert client.get("/api/cab/audition").json()["open"] is False
    _no_store_no_cab_wire(rig)


def test_a_failed_start_whose_restore_also_fails_stays_open(client, rig, monkeypatch):
    """Review round 1: the start path used to clear a FIRST audition's session
    even when putting the original back had not landed, so the unit sat on
    a wrong cab with nothing on record. Same rule as /end: not restored
    means open, with the error, until a retry lands."""
    before = server._read_cab(rig)
    real = server._select_cab
    calls = {"n": 0}

    def wrong_then_wrong_then_right(fm9, bank, ordinal, instance=1):
        calls["n"] += 1
        if calls["n"] == 1:                    # the audition write
            real(fm9, 3, 40, instance)
            return False, before, (3, 40)
        if calls["n"] == 2:                    # the restore, not landing
            return False, (3, 40), (3, 40)
        return real(fm9, bank, ordinal, instance)

    monkeypatch.setattr(server, "_select_cab", wrong_then_wrong_then_right)
    r = client.post("/api/cab/audition", json={"bank": 3, "ordinal": 46})
    assert r.status_code == 502 and r.json()["restored"] is False
    st = r.json()["audition"]
    assert st["open"] is True and "restore" in st["last_error"]
    assert (st["original"]["bank"], st["original"]["ordinal"]) == before
    r = client.post("/api/cab/audition/end")
    assert r.status_code == 200 and server._read_cab(rig) == before
    assert client.get("/api/cab/audition").json()["open"] is False
    _no_store_no_cab_wire(rig)


def test_a_failed_restore_keeps_the_session_open_for_retry(client, rig, monkeypatch):
    """Design review F1.2: the unit is never silently left on the audition
    cab. A restore that does not land stays open with last_error; the
    retry through /end succeeds once the unit answers."""
    before = server._read_cab(rig)
    client.post("/api/cab/audition", json={"bank": 3, "ordinal": 40})
    real = server._select_cab
    calls = {"n": 0}

    def once_wrong(fm9, bank, ordinal, instance=1):
        calls["n"] += 1
        if calls["n"] == 1:
            return False, (3, 40), (3, 40)
        return real(fm9, bank, ordinal, instance)

    monkeypatch.setattr(server, "_select_cab", once_wrong)
    r = client.post("/api/cab/audition/end")
    assert r.status_code == 502
    st = r.json()["audition"]
    assert st["open"] is True and "restore" in st["last_error"]
    r = client.post("/api/cab/audition/end")
    assert r.status_code == 200 and server._read_cab(rig) == before
    assert client.get("/api/cab/audition").json()["open"] is False


def test_only_a_plan_commits_a_cab(client, rig):
    """Committing is set_cab through run_action, verified by read-back as
    today; an audition left open is not a commit and a plan start closes it."""
    before = server._read_cab(rig)
    client.post("/api/cab/audition", json={"bank": 3, "ordinal": 46})
    with server._lock:
        server._audition_restore_if_open("plan start")
    assert server._read_cab(rig) == before
    assert server._audition["open"] is False
    res = server.run_action(rig, server.Action(kind="set_cab", block="cab",
                                               bank=3, value=46))
    assert res["ok"] and server._read_cab(rig) == (3, 46)


# --- #84 cleanup ------------------------------------------------------------

def test_cleanup_leaves_no_leftovers(client, rig, monkeypatch, tmp_path):
    """Nothing audition-shaped survives an audition with no pick: the block
    points where it did, the sim's user-cab store is byte-identical, and the
    user-cab name registry is unchanged."""
    from fm9 import user_cabs
    monkeypatch.setattr(user_cabs, "PATH", tmp_path / "user_cabs.json",
                        raising=False)
    store_before = {k: [list(c) for c in v]
                    for k, v in getattr(rig.sim_core, "user_cabs", {}).items()}
    names_before = user_cabs.load() if hasattr(user_cabs, "load") else None
    before = server._read_cab(rig)
    for ordn in (40, 46, 50):
        assert client.post("/api/cab/audition",
                           json={"bank": 3, "ordinal": ordn}).status_code == 200
    assert client.post("/api/cab/audition/end").status_code == 200
    assert server._read_cab(rig) == before
    store_after = {k: [list(c) for c in v]
                   for k, v in getattr(rig.sim_core, "user_cabs", {}).items()}
    assert store_after == store_before
    if names_before is not None:
        assert user_cabs.load() == names_before
    _no_store_no_cab_wire(rig)


def test_cleanup_on_device_drop_restores_first(client, rig):
    before = server._read_cab(rig)
    client.post("/api/cab/audition", json={"bank": 3, "ordinal": 40})
    server.drop_fm9()
    assert rig.get_param_wire(server.reg.spec("CABINET", 4, 1)) == before[1]
    assert server._audition["open"] is False


def test_cleanup_scratch_slot_is_documented():
    text = (Path(__file__).resolve().parent.parent / "config" / "README.md").read_text()
    assert "scratch" in text.lower() and "TONECOMMAND_CAB_SLOTS" in text
