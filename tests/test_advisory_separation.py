"""Issues #71 (open-ended brainstorm) and #72 (advisory/action separation).

Two different gaps, one theme: nothing said in a conversation should be able
to change hardware unless the player made an explicit build/change request.

#72 closes a CODE gap: fm9/planner.py already prompted the model not to mix
a clarification with actions, but nothing enforced it if the model did
anyway. #71 closes a UI-routing gap: an open-ended, undecided message had no
path to the conversation at all - it fell straight into the build path
because requestRoute() only ever returned 'source', 'build', or 'modify'.

The UI has no JS test runner in this repo; existing tests (tests/test_chat.py,
e.g. test_button_and_enter_both_use_the_single_request_router) verify
frontend logic by slicing the relevant function's source out of
ui/index.html and asserting on it. These tests follow that same convention.
"""
from __future__ import annotations

import re
from pathlib import Path

from fm9 import planner

ROOT = Path(__file__).resolve().parent.parent
UI = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")


# --- #72: clarification forces actions empty, regardless of the model -----

def test_clarification_forces_actions_empty_even_if_model_returns_actions():
    plan_obj = {
        "summary": "would need one more detail",
        "clarification": "Which amp channel should the lead scene use?",
        "actions": [{"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE",
                     "value": 7.0, "instance": 1}],
    }
    out = planner._validate(plan_obj)
    assert out["actions"] == []
    assert out["clarification"]


def test_a_plan_with_no_clarification_keeps_its_actions():
    plan_obj = {
        "summary": "raise the lead gain",
        "clarification": None,
        "actions": [{"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE",
                     "value": 7.0, "instance": 1}],
    }
    out = planner._validate(plan_obj)
    assert len(out["actions"]) == 1


def test_an_empty_or_blank_clarification_does_not_wipe_actions():
    # A falsy-but-present clarification ("", whitespace) is not a real
    # question; only a non-empty one means "nothing has been decided yet".
    for blank in ("", "   ", None):
        plan_obj = {"summary": "x", "clarification": blank,
                    "actions": [{"kind": "set_param", "block": "amp",
                                 "param": "DISTORT_DRIVE", "value": 7.0, "instance": 1}]}
        out = planner._validate(plan_obj)
        assert len(out["actions"]) == 1, f"blank clarification {blank!r} should not clear actions"


# --- #71: an open-ended, undecided message routes to chat, not the build path

def _extract_fn(name: str) -> str:
    marker = f"function {name}("
    start = UI.index(marker)
    depth = 0
    i = UI.index("{", start)
    j = i
    while True:
        if UI[j] == "{":
            depth += 1
        elif UI[j] == "}":
            depth -= 1
            if depth == 0:
                return UI[start:j + 1]
        j += 1


def test_open_ended_ambiguous_input_routes_to_chat_not_build():
    fn = _extract_fn("requestRoute")
    assert "'chat'" in fn, "requestRoute must be able to return a 'chat' route"
    # The open-ended check must run before the empty-grid/build-keyword
    # check, so uncertainty wins when a message carries both.
    chat_idx = fn.index("'chat'")
    build_idx = fn.index("'build'")
    assert chat_idx < build_idx, "open-ended detection must be checked before 'build'"


def test_submit_request_sends_the_chat_route_to_talk_not_engage():
    fn = _extract_fn("submitRequest")
    assert re.search(r"requestRoute\(said\)\s*===\s*'chat'\)\s*\{\s*talk\(\)", fn), (
        "submitRequest must call talk() when requestRoute(said) is 'chat', "
        "before it ever reaches engage()")


def test_canonical_open_ended_phrases_are_recognised():
    fn = _extract_fn("requestRoute")
    m = re.search(r"const OPEN_ENDED_RE = (/.+/i);", UI)
    assert m, "OPEN_ENDED_RE must be defined at module scope for requestRoute to use"
    # Mirror the pattern in Python to prove it actually matches the
    # canonical open-ended phrasing from issue #71's own story ("brainstorm
    # about my rig ... until I'm ready to build").
    pattern = m.group(1)[1:-2]  # strip the JS /.../i delimiters
    rx = re.compile(pattern, re.IGNORECASE)
    for phrase in ("not sure what I want yet", "no idea where to start",
                   "what do you think would sound good", "let's brainstorm a bit"):
        assert rx.search(phrase), f"{phrase!r} should be recognised as open-ended"
    for phrase in ("build a Metallica rig", "make the lead louder"):
        assert not rx.search(phrase), f"{phrase!r} is an explicit request, not open-ended"
