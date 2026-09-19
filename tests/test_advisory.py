"""Epic A, the deterministic half: #68 compare, #69 close the gap, #70
diagnose, and the chat routing that puts their numbers under the model's
prose. Everything runs on the simulator; nothing here reaches hardware and
nothing here can produce an action."""
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import advisory as adv
from fm9 import editbuffer, planner
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
RULES = (ROOT / "config" / "tone_rules.md").read_text()


@pytest.fixture
def rig(monkeypatch):
    dev = SimFM9(server.reg)
    dev.status_dump()
    sent = []
    real = dev.outp.send
    monkeypatch.setattr(dev.outp, "send", lambda msg: (sent.append(msg), real(msg)))
    dev.sent = sent
    monkeypatch.setattr(server, "_fm9", dev)
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    monkeypatch.setattr(server, "_snaps", {"undo": None, "a": None, "b": None})
    return dev


def amp(dev, label, value):
    spec = server.reg.find_param("DISTORT", label)
    assert spec is not None, label
    r = dev.set_param_display(spec, value)
    assert r.ok, r


def scene2_amp(dev, label, value):
    """Parameters live on the CHANNEL, not the scene: to make scene 2 sound
    different, point its amp at channel B and set the value there."""
    dev.set_scene(2)
    dev.set_channel(server.reg.effect_id("DISTORT"), 1)
    amp(dev, label, value)
    dev.set_scene(1)


def cap(dev):
    import time
    time.sleep(0.35)          # the sim, like the unit, applies writes after a settle
    return editbuffer.capture(dev, server.reg)


# --- #68 compare ---------------------------------------------------------------

def test_compare_identical_captures_narrate_no_difference(rig):
    a = cap(rig)
    assert adv.compare(a, cap(rig), server.reg) == []
    assert adv.narrate([], "X", "Y") == ["X and Y are the same: no difference."]


def test_compare_names_amp_params_cab_and_engagement(rig):
    a = cap(rig)
    amp(rig, "Gain", 5.5); amp(rig, "Bass", 4)
    rig.set_bypass(server.reg.effect_id("DELAY"), True)
    b = cap(rig)
    diffs = adv.compare(a, b, server.reg)
    kinds = {(d.kind, d.block, d.label) for d in diffs}
    assert ("param", "DISTORT", "Gain") in kinds and ("param", "DISTORT", "Bass") in kinds
    assert ("engaged", "DELAY", "DELAY 1 engaged") in kinds
    lines = "\n".join(adv.narrate(diffs, "A", "B"))
    assert "B has more amp gain (5.5 vs 0)" in lines
    assert "B has Delay 1 off; A has it on" in lines


def test_compare_cab_change_is_named_through_the_registry(rig):
    a = cap(rig)
    res = server.run_action(rig, server.Action(kind="set_cab", block="cab", bank=3, value=40))
    assert res["ok"]
    b = cap(rig)
    d = [x for x in adv.compare(a, b, server.reg) if x.kind == "cab"]
    assert len(d) == 1 and "V30" in str(d[0].b)
    line = [l for l in adv.narrate(adv.compare(a, b, server.reg), "A", "B") if "cab" in l][0]
    assert "1960B V30" in line


def test_compare_amp_model_is_named_through_the_roster(rig):
    a = cap(rig)
    ordn, label = server.resolve_type_ordinal("DISTORT", server.reg.amp_roster["3"])
    rig.set_param_ordinal(server.reg.spec("DISTORT", 10, 1), ordn)
    b = cap(rig)
    d = [x for x in adv.compare(a, b, server.reg) if x.kind == "type" and x.block == "DISTORT"]
    assert len(d) == 1 and d[0].b == label


def test_compare_reports_a_block_present_on_one_side_only(rig):
    a = cap(rig)
    b = cap(rig)
    b["blocks"] = [x for x in b["blocks"] if x["family"] != "REVERB"]
    d = [x for x in adv.compare(a, b, server.reg) if x.kind == "presence"]
    assert d and d[0].block == "REVERB" and d[0].a is True and d[0].b is False
    assert "only in A" in adv.narrate(d, "A", "B")[0]


def test_compare_channel_change_and_active_channel_values(rig):
    a = cap(rig)
    rig.set_channel(server.reg.effect_id("DISTORT"), 1)
    amp(rig, "Gain", 7)                                  # written on channel B
    b = cap(rig)
    diffs = adv.compare(a, b, server.reg)
    assert any(d.kind == "channel" and d.block == "DISTORT" and d.b == "B" for d in diffs)
    gain = [d for d in diffs if d.kind == "param" and d.label == "Gain"]
    assert gain and gain[0].b == 7 and gain[0].channel == 0, "A's active channel is read for A"


def test_compare_uncalibrated_parameter_is_compared_raw(rig):
    a = cap(rig)
    b = cap(rig)
    blk = [x for x in b["blocks"] if x["family"] == "DISTORT"][0]
    stride = len(blk["values"]) // max(1, blk["channels"])
    pid = next(p for p in range(stride)
               if (server.reg.spec("DISTORT", p, 1) is None
                   or server.reg.spec("DISTORT", p, 1).dmin is None) and p != 10)
    blk["values"][pid] = (blk["values"][pid] + 1) % 65534
    d = [x for x in adv.compare(a, b, server.reg) if x.kind == "param" and x.param_id == pid]
    assert d and d[0].raw and "raw" in adv.narrate(d)[0]


def test_compare_route_sources_and_scene_restore(rig):
    client = TestClient(server.app)
    scene2_amp(rig, "Gain", 8)
    before = cap(rig)
    n = len(rig.sent)
    r = client.post("/api/advise/compare", json={"a": "scene 1", "b": "scene 2"})
    assert r.status_code == 200, r.text
    assert r.json()["a"].startswith("scene 1") and r.json()["b"].startswith("scene 2")
    assert rig.scene_name()[0] == 1, "the original scene is back"
    assert cap(rig) == before, "nothing but the scene selection moved"
    new = [list(m.data) for m in rig.sent[n:] if m.type == "sysex"]
    writes = [f for f in new if f[4] == 0x0C and f[5] != 0x7F]        # scene select is fn 0x0C
    assert all(f[4] == 0x0C for f in writes), "only scene selects were written"
    assert client.post("/api/advise/compare", json={"a": "scene 1", "b": "scene 9"}).status_code == 404
    assert client.post("/api/advise/compare", json={"a": "snapshot a", "b": "scene 1"}).status_code == 404
    server._take("a")
    assert client.post("/api/advise/compare", json={"a": "snapshot:a", "b": "scene 1"}).status_code == 200


# --- #69 gap ----------------------------------------------------------------------

def test_gap_gives_advice_with_from_to_and_a_build_prompt(rig):
    a = cap(rig)
    amp(rig, "Gain", 7.2)
    server.run_action(rig, server.Action(kind="set_cab", block="cab", bank=3, value=40))
    b = cap(rig)
    advice, prompt = adv.gap(adv.compare(a, b, server.reg), server.reg)
    params = {(x.block, x.param): (x.frm, x.to) for x in advice}
    assert params[("DISTORT", "Gain")] == (0, 7.2)
    assert ("CABINET", "cab") in params
    assert "set distort 1 gain to 7.2" in prompt and "select cab" in prompt
    assert not any(hasattr(x, "kind") for x in advice), "advice items are not actions"


def test_gap_route_has_no_actions_and_advisory_imports_no_executor(rig):
    client = TestClient(server.app)
    scene2_amp(rig, "Gain", 6)
    r = client.post("/api/advise/gap", json={"a": "scene 1", "b": "scene 2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "actions" not in body and body["advice"] and body["build_prompt"]
    src = inspect.getsource(adv)
    assert "run_action" not in src and "import server" not in src and "Action(" not in src


def test_gap_build_this_goes_through_plan_with_validation_and_confirm(rig, monkeypatch):
    """BUILD THIS is an ordinary plan request. The PLANNER is stubbed, not
    _plan_for, so the request runs the real validate_action pass and lands
    as a reviewed revision (plan_digest) that nothing applies."""
    client = TestClient(server.app)
    scene2_amp(rig, "Gain", 6)
    prompt = client.post("/api/advise/gap", json={"a": "scene 1", "b": "scene 2"}).json()["build_prompt"]
    seen = []

    def fake_plan(text, device_state, reference):
        seen.append(text)
        if len(seen) == 1:
            return {"summary": "x", "actions": [
                {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
                {"kind": "set_param", "block": "amp", "param": "NO_SUCH_PARAM", "value": 1.0}]}
        return {"summary": "repair", "actions": []}     # the repair round gives up

    monkeypatch.setattr(planner, "plan", fake_plan)
    r = client.post("/api/plan", json={"prompt": prompt})
    assert r.status_code == 200, r.text
    assert seen[0] == prompt, "the sentence went to the planner as typed"
    assert len(seen) >= 2 and "refused by validation" in seen[1], \
        "validate_action ran inside _plan_for and refused the bad action"
    plan = r.json()
    assert plan["plan_digest"], "a reviewed revision was minted"
    bad = [a for a in plan["actions"] if a.get("param") == "NO_SUCH_PARAM"]
    assert all(a.get("validation_errors") for a in bad), "a refused action stays marked, never silently accepted"
    assert adv.value(server.reg, cap(rig), "DISTORT", adv.AMP["gain"]) == 0, "nothing was applied"


# --- #70 diagnose ---------------------------------------------------------------------

def test_diagnose_every_rule_cited_exists_in_the_rulebook():
    ids = {c.rule for _s, checks in adv.SYMPTOMS.values() for c in checks}
    for rule in ids:
        n = rule.split()[1]
        assert re.search(rf"^## {n}\. ", RULES, re.M), f"{rule} is not in config/tone_rules.md"


@pytest.mark.parametrize("symptom", sorted(adv.SYMPTOMS))
def test_diagnose_each_symptom_has_a_likely_and_a_cleared_case(rig, symptom):
    # a clean, bright, dry starting point clears most checks
    amp(rig, "Bass", 5); amp(rig, "Mid", 5); amp(rig, "Treble", 5); amp(rig, "Presence", 5)
    amp(rig, "Gain", 5); amp(rig, "Master Volume", 5); amp(rig, "Depth", 5)
    amp(rig, "Low Cut Frequency", 90); amp(rig, "High Cut Frequency", 9000)
    base = adv.diagnose(cap(rig), symptom, server.reg)
    assert base["cleared"], symptom
    # push one knob per symptom into its likely zone
    push = {"muddy": ("Bass", 9), "boomy": ("Depth", 9), "thin": ("Mid", 1), "harsh": ("Presence", 9),
            "fizzy": ("High Cut Frequency", 18000), "dark": ("Treble", 1), "buried": ("Mid", 1)}[symptom]
    amp(rig, *push)
    out = adv.diagnose(cap(rig), symptom, server.reg)
    assert out["likely"], symptom
    likely = out["likely"][0]
    assert likely["verdict"] == "likely" and likely["value"] is not None and likely["rule"].startswith("rule ")
    assert "reads" in likely["wording"] and out["advice"] and len(out["advice"]) <= 3


def test_diagnose_reports_not_readable_when_a_block_is_missing(rig):
    c = cap(rig)
    c["blocks"] = [b for b in c["blocks"] if b["family"] != "DISTORT"]
    out = adv.diagnose(c, "harsh", server.reg)
    assert any(x["verdict"] == "not readable" for x in out["not_readable"])
    assert not any(x["check"].startswith("amp_") for x in out["likely"] + out["cleared"])


def test_diagnose_unknown_symptom_is_refused_with_the_list(rig):
    with pytest.raises(ValueError, match="wobbly"):
        adv.diagnose(cap(rig), "wobbly", server.reg)
    r = TestClient(server.app).post("/api/advise/diagnose", json={"symptom": "wobbly"})
    assert r.status_code == 400 and "muddy" in r.json()["symptoms"]


def test_diagnose_route_answers_for_the_loaded_scene_and_a_named_scene(rig):
    client = TestClient(server.app)
    amp(rig, "Bass", 9)
    r = client.post("/api/advise/diagnose", json={"symptom": "muddy"})
    assert r.status_code == 200 and any(x["check"] == "amp_bass_high" for x in r.json()["likely"])
    r = client.post("/api/advise/diagnose", json={"symptom": "muddy", "scene": "scene 1"})
    assert r.status_code == 200 and r.json()["scene"].startswith("scene 1")
    assert "actions" not in r.json()


# --- chat routing -------------------------------------------------------------------

@pytest.fixture
def stub_model(monkeypatch):
    seen = {}

    def fake_converse(messages, device_state, reference):
        seen["context"] = device_state
        return {"reply": "ok", "ready": False, "request": "", "name": "", "scenes": []}

    def fake_stream(messages, device_state, reference, cancel=None):
        seen["stream_context"] = device_state
        yield "text", "ok"
        yield "done", {"reply": "ok", "ready": False, "request": "", "name": "", "scenes": []}

    monkeypatch.setattr(planner, "converse", fake_converse)
    monkeypatch.setattr(planner, "converse_stream", fake_stream)
    monkeypatch.setattr(server, "_hold_settings", lambda cancel, on_status: True)
    return seen


def _chat(client, text):
    return client.post("/api/chat", json={"messages": [{"role": "user", "content": text}]})


def test_chat_diagnose_puts_measured_findings_under_the_model(rig, stub_model):
    client = TestClient(server.app)
    rig.rename_scene(1, "Rhythm")
    amp(rig, "Bass", 9)
    r = _chat(client, "why does my rhythm sound muddy?")
    assert r.status_code == 200, r.text
    ctx = stub_model["context"]
    assert ctx.startswith("ADVISORY FINDINGS (measured)\nkind: diagnose")
    assert "amp bass is high" in ctx and "[rule 9]" in ctx and "reads 9" in ctx
    assert r.json()["advisory"]["diagnosis"]["symptom"] == "muddy"
    assert "actions" not in r.json()


def test_chat_compare_and_gap_shapes_route(rig, stub_model):
    client = TestClient(server.app)
    scene2_amp(rig, "Gain", 8)
    r = _chat(client, "What's the difference between scene 1 and scene 2?")
    assert "kind: compare" in stub_model["context"] and "more amp gain (8 vs 0)" in stub_model["context"]
    assert r.json()["advisory"]["comparison"]["b"].startswith("scene 2")
    r = _chat(client, "how do I get scene 1 closer to scene 2")
    assert "kind: gap" in stub_model["context"] and "Advice only" in stub_model["context"] or "These are advice" in stub_model["context"]
    assert r.json()["advisory"]["gap"]["build_prompt"]


@pytest.mark.parametrize("text", [
    "the difference is subtle, I like both",
    "move it closer to the amp",
    "this is muddy but I like it",
    "why does my cat sound muddy",          # not a scene of the loaded preset
    "what's the difference between a Marshall and a Mesa",
])
def test_chat_ordinary_messages_reach_the_model_unchanged(rig, stub_model, text):
    client = TestClient(server.app)
    r = _chat(client, text)
    assert r.status_code == 200
    assert not stub_model["context"].startswith("ADVISORY FINDINGS")
    assert "advisory" not in r.json()


def test_chat_stream_uses_the_same_routing_and_context(rig, stub_model):
    client = TestClient(server.app)
    rig.rename_scene(1, "Rhythm")
    amp(rig, "Bass", 9)
    msgs = {"messages": [{"role": "user", "content": "why does my rhythm sound muddy?"}]}
    _chat(client, msgs["messages"][0]["content"])
    r = client.post("/api/chat/stream", json=msgs)
    assert r.status_code == 200
    assert stub_model["stream_context"] == stub_model["context"]
    assert '"advisory"' in r.text and '"diagnosis"' in r.text


@pytest.mark.parametrize("text,kind", [
    ("why does my rhythm sound muddy", "diagnose"),
    ("why is my lead so thin?", "diagnose"),
    ("hey, why does my scene 3 sound harsh", "diagnose"),
    ("what's the difference between scene 1 and scene 2", "compare"),
    ("difference between snapshot a and snapshot b?", "compare"),
    ("how do scene 1 and scene 2 differ", "compare"),
    ("scene 1 and design Night differ", "compare"),
    ("how do I get scene 1 closer to scene 2", "gap"),
    ("make scene 1 closer to scene 2", "gap"),
    ("closer to scene 2", "gap"),
])
def test_chat_the_three_shapes_are_recognised(text, kind):
    assert adv.parse_question(text)["kind"] == kind


@pytest.mark.parametrize("text", [
    "why does the rhythm sound muddy",          # 'the' is not the stated form
    "why does my rhythm sound muddy today",     # trailing words: not the shape
    "I wonder why does my rhythm sound muddy",  # not at the start
    "what is the difference between them",      # one object
    "differences are fine",
    "scene 1 differs a lot",                    # no 'and'
    "closer to",
])
def test_chat_shapes_outside_the_spec_are_not_routed(text):
    assert adv.parse_question(text) is None


def test_chat_a_gap_with_an_unresolvable_object_is_not_routed(rig, stub_model):
    client = TestClient(server.app)
    r = _chat(client, "is it closer to done?")
    assert r.status_code == 200 and "advisory" not in r.json()
    assert not stub_model["context"].startswith("ADVISORY FINDINGS")


def test_chat_precedence_diagnose_before_compare_before_gap():
    assert adv.parse_question("why does my scene 1 sound thin")["kind"] == "diagnose"
    assert adv.parse_question("difference between scene 1 and scene 2")["kind"] == "compare"
    assert adv.parse_question("closer to scene 2")["kind"] == "gap"
    assert adv.parse_question("why does my rhythm sound wonderful") is None
