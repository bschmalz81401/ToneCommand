"""Issue #104 (G5): catch AND propose a fix, human-confirmed. propose() and
rounds() are pure; the routes measure and propose; nothing here sends.
Round proofs run on the simulator with a fake recorder whose loudness
follows the sim's own OUTPUT_SCENEn trims, so a move that is applied
changes the next measurement to the dB.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest
from fastapi.testclient import TestClient

import server
from fm9 import advisory, editbuffer, measure as M, sound_check as sc
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
UI = (ROOT / "ui" / "index.html").read_text()
RATE = 48000


def _balance(*rows):
    return M.scene_balance([{"scene": s, "role": r, "lufs": l} for s, r, l in rows])


# --- REQ-001: propose ------------------------------------------------------------------------

def test_propose_one_output_scene_move_per_measurable_finding():
    bal = _balance((1, "clean", -26.0), (2, "rhythm", -23.0), (3, "lead", -18.0), (4, "lead", -21.7))
    p = sc.propose(bal, {1: 0.0, 2: 0.0, 3: 2.0, 4: 0.5})
    by = {f["scene"]: f for f in p["fixes"]}
    assert set(by) == {1, 3, 4} and p["unfixable"] == []
    assert by[1]["delta_db"] == 3.0 and by[1]["actions"][0]["value"] == 3.0      # clean 3 LU under: +3
    assert by[3]["delta_db"] == -2.0 and by[3]["actions"][0]["value"] == 0.0     # lead 5 LU over: to +3
    assert by[3]["rule"] == "cap_4lu_above_rhythm"
    assert by[4]["delta_db"] == 1.0 and by[4]["actions"][0]["value"] == 1.5      # lead +1.3: to 2.5, rounded
    for f in p["fixes"]:
        a = f["actions"][0]
        assert a["kind"] == "set_param" and a["block"] == "OUTPUT" and a["param"] == f"OUTPUT_SCENE{f['scene']}"
        assert f["how"] == "actions" and f["clamped"] is False and f["note"] is None
        assert bal["baseline"] in a["reason"] and f["reason"] in [x["line"] for x in bal["findings"]]
    assert [a["param"] for a in p["actions"]] == [f["actions"][0]["param"] for f in p["fixes"]]
    assert p["summary"] == "sound check: 3 moves to balance the scenes"


def test_propose_moves_cannot_fight_and_a_scene_appears_once():
    # a lead 5 LU over trips both the cap and the lead-band rule: one move, the cap's
    bal = _balance((2, "rhythm", -23.0), (3, "lead", -18.0))
    assert [f["rule"] for f in bal["findings"]] == ["cap_4lu_above_rhythm"]
    p = sc.propose(bal, {2: 0.0, 3: 0.0})
    assert len(p["fixes"]) == 1 and p["fixes"][0]["delta_db"] == -2.0
    # every target is against the same median, so applying the moves balances in one go
    bal2 = _balance((1, "clean", -27.0), (2, "rhythm", -23.0), (3, "rhythm", -23.0), (4, "lead", -24.0))
    p2 = sc.propose(bal2, {1: 0, 2: 0, 3: 0, 4: 0})
    after = {s: l for s, _, l in ((1, "clean", -27.0), (2, "rhythm", -23.0), (3, "rhythm", -23.0), (4, "lead", -24.0))}
    for f in p2["fixes"]:
        after[f["scene"]] += f["delta_db"]
    assert _balance(*[(s, r, after[s]) for s, r, _ in ((1, "clean", 0), (2, "rhythm", 0), (3, "rhythm", 0), (4, "lead", 0))])["findings"] == []


def test_propose_clamps_at_the_trim_range_and_names_the_amp_caveat():
    bal = _balance((1, "clean", -26.0), (2, "rhythm", -23.0))
    p = sc.propose(bal, {1: 18.5, 2: 0.0})
    f = p["fixes"][0]
    assert f["clamped"] is True and f["actions"][0]["value"] == 20.0
    assert f["note"].startswith("the trim runs out 1.5 dB short") and "DISTORT_LEVEL" in f["note"]
    low = sc.propose(_balance((2, "rhythm", -23.0), (5, "other", -15.0)), {2: 0.0, 5: -19.0})
    assert low["fixes"][0]["actions"][0]["value"] == -20.0 and low["fixes"][0]["clamped"]


def test_propose_no_baseline_is_unfixable_and_style_is_never_a_move():
    p = sc.propose(_balance((1, "clean", -23.0)), {1: 0.0})
    assert p["fixes"] == [] and p["actions"] == [] and "no rhythm scene" in p["summary"]
    bal = _balance((1, "clean", -26.0), (2, "rhythm", -23.0))
    bal["findings"].append({"rule": "fizz_above_8k", "cls": "style", "line": "scene 1 fizz", "baseline": "x",
                            "value": 0, "limit": None})
    p = sc.propose(bal, {1: 0.0, 2: 0.0})
    assert [f["rule"] for f in p["fixes"]] == ["clean_not_below_rhythm"]
    # a scene whose trim was not read is said, not guessed
    p = sc.propose(bal, {2: 0.0})
    assert p["fixes"] == [] and p["unfixable"][0]["why"].startswith("the scene's OUTPUT_SCENEn trim")
    src = (ROOT / "fm9" / "sound_check.py").read_text()
    assert "set_param(" not in src and "store_preset" not in src


# --- REQ-002: rounds --------------------------------------------------------------------------

def test_rounds_count_only_proposals_and_end_clean_or_by_cap():
    st = sc.new_state()
    bal = _balance((1, "clean", -26.0), (2, "rhythm", -23.0))
    p = sc.propose(bal, {1: 0.0, 2: 0.0})
    sc.rounds(st, bal, p)
    assert st["round"] == 1 and st["done"] is False and p["round"] == 1
    clean = _balance((1, "clean", -23.0), (2, "rhythm", -23.0))
    p2 = sc.propose(clean, {1: 3.0, 2: 0.0})
    sc.rounds(st, clean, p2)
    assert st["done"] is True and st["ended_by"] == "clean" and p2["summary"] == "sound check: balanced after 1 round"
    st2 = sc.new_state()
    for _ in range(3):
        sc.rounds(st2, bal, sc.propose(bal, {1: 0.0, 2: 0.0}))
    assert st2["round"] == 3 and not st2["done"]
    p4 = sc.propose(bal, {1: 0.0, 2: 0.0})
    sc.rounds(st2, bal, p4)
    assert st2["done"] and st2["ended_by"] == "cap"
    assert p4["summary"].startswith("after 3 rounds these remain: scene 1 (clean) is 3.0 LU below")
    assert p4["actions"] == [] and len(st2["history"]) == 4


def _fake_recorder(sim, base_lufs: dict, masked: set[int] = frozenset()):
    """LUFS follows the scene's OUTPUT_SCENEn trim on the sim, to the dB,
    unless the scene is masked (the amp is the bottleneck: the trim does
    nothing)."""
    def recorder(signal, seconds, out_channel):
        n = sim.scene_name()[0]
        cap = editbuffer.capture(sim, server.reg)
        trim = advisory.value(server.reg, cap, "OUTPUT", sc.OUTPUT_SCENE_PID[n]) or 0.0
        lufs = base_lufs[n] + (0.0 if n in masked else trim)
        # a -23 dBFS 1 kHz tone reads -23 LUFS; scale to the wanted loudness
        t = np.arange(int(seconds * RATE)) / RATE
        y = (10 ** ((lufs) / 20) * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        return np.stack([y, y], 1)
    return recorder


def _apply(sim, actions):
    """Stands in for /api/apply after Confirm: the same set_param the
    server's run_action performs, on the simulator."""
    for a in actions:
        spec = server.reg.spec(a["block"], int(a["param"].replace("OUTPUT_SCENE", "")) + 17, 1)
        assert spec.name == a["param"]
        sim.set_param_display(spec, float(a["value"]))


@pytest.fixture
def sim(monkeypatch, tmp_path):
    monkeypatch.setenv("TONECOMMAND_CAPTURES", str(tmp_path / "captures"))
    monkeypatch.setenv("TONECOMMAND_ROUTING_JOURNAL", str(tmp_path / "journal.json"))
    from fm9 import routing
    routing.OBSERVED.clear()
    routing.observe(routing.IN1_SOURCE, 0, "ANALOG"); routing.observe(routing.IN1_SOURCE, 1, "DIGITAL")
    routing.observe(routing.DIGITAL_SOURCE, 1, "AES"); routing.observe(routing.DIGITAL_SOURCE, 2, "USB")
    dev = SimFM9(server.reg)
    dev.status_dump()
    for n, pid in sc.OUTPUT_SCENE_PID.items():           # a real preset's trims sit at 0 dB
        dev.set_param_display(server.reg.spec("OUTPUT", pid, 1), 0.0)
    assert server._output_trims(dev) == {n: 0.0 for n in range(1, 9)}
    monkeypatch.setattr(server, "_fm9", dev)
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    server._sound_check.update({"preset": None, "state": sc.new_state()})
    return dev


def _remeasure(client, roles):
    return client.post("/api/sound-check/remeasure",
                       json={"scenes": [{"scene": s, "role": r} for s, r in roles.items()]})


def test_remeasure_rounds_a_proposed_move_lands_and_round_two_is_clean(sim, monkeypatch, tmp_path):
    base = {1: -26.0, 2: -23.0, 3: -18.0}
    monkeypatch.setattr(server, "_sound_check_recorder", lambda: _fake_recorder(sim, base))
    c = TestClient(server.app)
    roles = {1: "clean", 2: "rhythm", 3: "lead"}
    from fm9 import routing
    before = {k: v["ordinal"] for k, v in routing.read_routing(sim).items()}
    r = _remeasure(c, roles)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["state"]["round"] == 1 and sorted(f["scene"] for f in d["proposal"]["fixes"]) == [1, 3]
    assert d["captured"]["returned"] is True and d["captured"]["origin_scene"] == 1
    assert len(list((tmp_path / "captures").glob("test-*-s*.wav"))) == 3
    # the player confirms; the plan path applies; SOUND CHECK again
    _apply(sim, d["proposal"]["actions"])
    r2 = _remeasure(c, roles).json()
    assert r2["balance"]["findings"] == [] and r2["state"]["done"] is True and r2["state"]["ended_by"] == "clean"
    assert r2["proposal"]["summary"] == "sound check: balanced after 1 round" and r2["proposal"]["actions"] == []
    assert abs(r2["balance"]["scenes"][0]["lufs"] - (-23.0)) < 0.2         # the clean landed on the median
    assert abs(r2["balance"]["scenes"][2]["delta_lu"] - 3.0) < 0.2         # the lead sits at the cap edge
    # routing came back to what it was, and the journal is gone
    assert {k: v["ordinal"] for k, v in routing.read_routing(sim).items()} == before
    assert not routing.journal_path().exists()


def test_remeasure_a_masked_scene_ends_by_the_cap_with_the_remaining_line(sim, monkeypatch):
    base = {1: -26.0, 2: -23.0}
    monkeypatch.setattr(server, "_sound_check_recorder", lambda: _fake_recorder(sim, base, masked={1}))
    c = TestClient(server.app)
    roles = {1: "clean", 2: "rhythm"}
    for expect in (1, 2, 3):
        d = _remeasure(c, roles).json()
        assert d["state"]["round"] == expect and d["proposal"]["actions"], d["proposal"]["summary"]
        _apply(sim, d["proposal"]["actions"])
    d = _remeasure(c, roles).json()
    assert d["state"]["done"] and d["state"]["ended_by"] == "cap"
    assert d["proposal"]["summary"].startswith("after 3 rounds these remain: scene 1 (clean) is 3.0 LU below")
    assert d["proposal"]["actions"] == []
    # the trim was pushed three times and the measurement never moved: reported, not chased
    trims = server._output_trims(sim)
    assert trims[1] == 9.0


def test_state_resets_on_a_preset_change(sim, monkeypatch):
    base = {1: -26.0, 2: -23.0}
    monkeypatch.setattr(server, "_sound_check_recorder", lambda: _fake_recorder(sim, base))
    c = TestClient(server.app)
    d = _remeasure(c, {1: "clean", 2: "rhythm"}).json()
    assert d["state"]["round"] == 1
    sim.select_preset(5)
    for n, pid in sc.OUTPUT_SCENE_PID.items():
        sim.set_param_display(server.reg.spec("OUTPUT", pid, 1), 0.0)
    d = _remeasure(c, {1: "clean", 2: "rhythm"}).json()
    assert d["state"]["round"] == 1 and len(d["state"]["history"]) == 1


# --- REQ-003: the route on captures, the plan shape, the page ------------------------------------

def test_route_sound_check_measures_captures_and_answers_the_fix_shape(sim, tmp_path):
    caps = tmp_path / "captures"; caps.mkdir()
    from fm9 import capture
    def tone(lufs):
        t = np.arange(4 * RATE) / RATE
        y = (10 ** (lufs / 20) * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
        return np.stack([y, y], 1)
    capture.write_wav(caps / "s1.wav", tone(-26.0)); capture.write_wav(caps / "s2.wav", tone(-23.0))
    c = TestClient(server.app)
    r = c.post("/api/sound-check", json={"captures": [
        {"scene": 1, "role": "clean", "path": str(caps / "s1.wav")},
        {"scene": 2, "role": "rhythm", "path": str(caps / "s2.wav")}]})
    assert r.status_code == 200, r.text
    d = r.json()
    f = d["proposal"]["fixes"][0]
    assert f["how"] == "actions" and f["label"].startswith("Scene 1 (clean) +3 dB on OUTPUT_SCENE1")
    assert d["proposal"]["actions"] == f["actions"] and d["state"]["round"] == 1
    assert d["balance"]["status"] == "concern"
    assert c.post("/api/sound-check", json={"captures": []}).status_code == 400
    assert c.post("/api/sound-check", json={"captures": [{"scene": 1, "role": "clean", "path": "/nowhere.wav"}]}).status_code == 400
    server._gig_mode["on"] = True
    assert c.post("/api/sound-check", json={"captures": [{"scene": 1, "role": "clean", "path": str(caps / "s1.wav")}]}).status_code == 423
    assert c.post("/api/sound-check/remeasure", json={"scenes": [{"scene": 1, "role": "clean"}]}).status_code == 423
    server._gig_mode["on"] = False


def test_the_page_proposes_through_show_plan_and_never_sends_itself():
    assert 'id="soundcheck"' in UI and "async function runSoundCheck" in UI
    assert "fetch('/api/sound-check/remeasure'" in UI
    assert "showPlan({summary: d.proposal.summary, actions: d.proposal.actions" in UI
    body = UI.split("async function runSoundCheck", 1)[1].split("$('scan').onclick", 1)[0] \
        if "$('scan').onclick" in UI.split("async function runSoundCheck", 1)[1] else \
        UI.split("async function runSoundCheck", 1)[1].split("let blockBusy", 1)[0]
    assert "/api/apply" not in body
    assert "function sceneRole" in UI and "rhythm|crunch|chug|rhy" in UI
