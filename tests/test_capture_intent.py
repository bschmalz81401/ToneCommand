"""#145 (I2): capture or model, the wording decides; the line never claims a
capture that was not used; rule 22 is in the rulebook the planner loads."""
import sys
from pathlib import Path

import pytest

sys.argv = ["x"]
from fm9 import capture_intent, nam
from fm9.capture_intent import amp_source, amp_source_intent, amp_source_line

ROOT = Path(__file__).resolve().parent.parent

CAPTURE = [
    "make it sound like the real amp", "I want the actual 5150, not a model", "give me a capture of a Friedman BE",
    "use my captured JCM800 for rhythm", "the real thing please", "load the NAM capture of the Recto",
    "The Real Marshall, loud", "use the capture for the lead",
]
MODEL = [
    "give me a base tone I can shape", "a starting point for a lead", "something I can tweak later",
    "just the stock model", "a base to build on", "use the Fractal model",
]
NEUTRAL = [
    "capture the vibe of a 70s stadium clean", "the actual settings matter less than the feel", "actually make it brighter",
    "a big lead tone", "", None, "make it darker", "the model number of my amp is on the back",
    "a capture preset would be nice", "my captures are on the laptop", "something like the real deal",
]


@pytest.mark.parametrize("words", CAPTURE)
def test_the_real_thing_wording_leans_to_a_capture(words):
    assert amp_source_intent(words) == "capture", words


@pytest.mark.parametrize("words", MODEL)
def test_starting_point_wording_leans_to_the_model(words):
    assert amp_source_intent(words) == "model", words


@pytest.mark.parametrize("words", NEUTRAL)
def test_ordinary_wording_is_no_lean(words):
    assert amp_source_intent(words) is None, words


def test_when_both_appear_the_last_phrase_wins_and_a_tie_is_none():
    assert amp_source_intent("start from the stock model, then make it the real thing") == "capture"
    assert amp_source_intent("I want the real amp but give me a base tone first") == "model"
    assert amp_source_intent("the capture") == "capture" and amp_source_intent("the model") == "model"


def test_matching_is_on_word_boundaries_and_case_insensitive():
    assert amp_source_intent("recaptured glory") is None          # 'captured' inside another word
    assert amp_source_intent("CAPTURED tone please") == "capture"
    assert amp_source_intent("basetone") is None and amp_source_intent("base  tone") == "model"


def _record(**meta):
    import json
    doc = {"architecture": "WaveNet", "config": {}, "weights": [0.1], "metadata": meta}
    return nam.read_nam(json.dumps(doc))


def test_the_line_names_the_source_and_never_claims_an_unused_capture():
    # no support, capture asked: the model with the honest reason
    assert amp_source_line("capture", False, None, "5153 100W Red") == "amp is the Fractal 5153 100W Red, no NAM support on this unit yet"
    # supported but nothing selected: still the model, a different honest reason
    assert amp_source_line("capture", True, None, "5153 100W Red") == "amp is the Fractal 5153 100W Red, no suitable capture on file"
    # model asked or no lean: the model, no suffix
    assert amp_source_line("model", True, None, "5153 100W Red") == "amp is the Fractal 5153 100W Red"
    assert amp_source_line(None, False, None, None) == "amp is the Fractal model already loaded"
    # only a used CaptureRecord yields the capture line
    used = _record(gear_make="Peavey", gear_model="5150", modeled_by="Moncy")
    assert amp_source_line("capture", True, used, "5153 100W Red") == "amp is a capture of a real Peavey 5150 by Moncy"
    bare = _record()
    assert amp_source_line(None, True, bare, None) == "amp is a capture of a real gear not on file (modeled_by not on file)"


def test_amp_source_payload_shape():
    got = amp_source("the real amp", capture_support=False, used_capture=None, model_name="Brit 800")
    assert got == {"intent": "capture", "kind": "model", "line": "amp is the Fractal Brit 800, no NAM support on this unit yet"}
    used = _record(gear_make="Marshall", gear_model="JCM800", modeled_by="B")
    assert amp_source("the real amp", capture_support=True, used_capture=used)["kind"] == "capture"


def test_the_server_attaches_amp_source_from_the_plans_own_amp_type():
    import server
    result = {"actions": [{"kind": "set_type", "block": "Amp", "instance": 1, "type_name": "USA Lead+"}]}
    assert server.plan_amp_source(result, "the actual recto") == {
        "intent": "capture", "kind": "model", "line": "amp is the Fractal USA Lead+, no NAM support on this unit yet"}
    assert server.plan_amp_source({"actions": []}, "a base tone") == {
        "intent": "model", "kind": "model", "line": "amp is the Fractal model already loaded"}
    src = (ROOT / "server.py").read_text(encoding="utf-8")
    assert src.count('result["amp_source"] = plan_amp_source(result, body.prompt)') == 2  # live and shared-profile paths


def test_rule_22_is_in_the_rulebook_the_planner_loads_and_21_is_still_one_rule():
    from fm9 import planner
    text = planner._load_tone_rules()
    assert text.count("## 21. ") == 1 and text.count("## 22. ") == 1
    assert "## 22. Capture or model: the wording decides, never a setting (issue #145)" in text
    assert "The plan never claims a\ncapture was used when it was not." in text.replace("\r", "")
    assert chr(0x2014) not in text
