"""Issue #98: a request with two opposed things in it must be named, not
silently resolved. Deterministic by design (see fm9/request_tension.py's
own docstring for why this cannot be a live model call in this run).
"""
from __future__ import annotations

from fm9 import request_tension as rt


def test_known_opposed_pairs_are_flagged_with_a_stated_lean():
    tensions = rt.detect("I want it tight but really warm and dark")
    assert len(tensions) == 1
    t = tensions[0]
    assert t.side_a == "tight"
    assert t.side_b in ("warm", "dark")
    assert t.lean == "tight"
    assert "rule 9" in t.reason


def test_a_request_with_no_opposition_is_not_flagged():
    assert rt.detect("give me a tight, cutting metal rhythm tone") == []
    assert rt.detect("build a big ambient clean with lots of space") == []


def test_multiple_documented_pairs_in_one_request_are_all_named():
    tensions = rt.detect("tight but dark, and quiet but huge, please")
    leans = {t.lean for t in tensions}
    assert "tight" in leans
    assert "loud" in leans
    assert len(tensions) == 2


def test_context_lines_are_empty_when_nothing_is_detected():
    assert rt.context_lines("build a Mesa rectifier lead tone") == []


def test_context_lines_state_the_tension_and_the_chosen_lean():
    lines = rt.context_lines("tight but warm and dark rhythm")
    joined = "\n".join(lines)
    assert "TENSION" in joined
    assert "tight" in joined.lower()
    assert "leaning" in joined.lower()


def test_matching_is_case_insensitive_and_whole_word():
    # "airtight" must not false-match "tight" as a substring.
    assert rt.detect("keep it airtight and warm") == []
    assert rt.detect("TIGHT but WARM") != []
