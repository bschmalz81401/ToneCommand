"""Issue #98: a request with two opposed things in it must be named, not
silently resolved. Deterministic by design (see fm9/request_tension.py's
own docstring for why this cannot be a live model call in this run).
"""
from __future__ import annotations

import pytest

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


# --- second pass: synonyms, deterministic matching, ordinary requests -------

def test_synonyms_catch_the_same_tension_in_other_words():
    t = rt.detect("punchy but woolly rhythm")
    assert len(t) == 1 and t[0].lean == "tight"
    assert (t[0].side_a, t[0].side_b) == ("punchy", "woolly")
    t = rt.detect("bone dry but cavernous")
    assert len(t) == 1 and t[0].lean == "wet"
    t = rt.detect("bedroom level but a wall of sound")
    assert len(t) == 1 and t[0].lean == "loud"
    t = rt.detect("retro voicing, brutal gain")
    assert len(t) == 1 and t[0].lean == "vintage voicing"


def test_bright_versus_dim_is_its_own_pair_and_leans_bright():
    t = rt.detect("sparkly but no top end")
    assert len(t) == 1 and t[0].lean == "bright"
    assert t[0].side_b == "no top end", "longest phrase is the one reported"


def test_muffled_and_woolly_belong_to_warm_only():
    # Design review F2.1: a word in two pairs would argue twice.
    assert rt.detect("bright but muffled") == []
    assert rt.detect("tight but muffled")[0].lean == "tight"


def test_no_term_belongs_to_more_than_one_pair():
    seen = {}
    for n, (a, b, _lean, _why) in enumerate(rt._PAIRS):
        for term in rt._expand(a) | rt._expand(b):
            assert term not in seen, f"{term!r} in pairs {seen[term]} and {n}"
            seen[term] = n
        assert not (rt._expand(a) & rt._expand(b))


def test_one_finding_per_pair_however_many_words_hit():
    t = rt.detect("tight, punchy and chunky, but warm, smooth and dark")
    assert len(t) == 1


def test_phrases_match_on_word_boundaries_only():
    assert rt.detect("a dry-ish but ambient clean") == []   # "dry-ish" is not "dry"
    assert rt.detect("no reverbs but ambient") == []        # "no reverb" needs a boundary
    assert rt.detect("no reverb, but ambient") != []


@pytest.mark.parametrize("request_text", [
    "a tight modern metal rhythm",
    "warm jazz clean with lush reverb",
    "a classic rock crunch with a bit of edge",
    "big 80s clean with chorus and lots of reverb",
    "bright country clean, spanky and snappy",
    "a dark doom rhythm, thick and heavy",
    "punchy djent rhythm with a tight low end",
    "smooth blues lead with some delay",
])
def test_ordinary_requests_detect_nothing(request_text):
    assert rt.detect(request_text) == []


def test_as_dicts_is_the_plan_result_shape():
    d = rt.as_dicts("tight but warm")
    assert d == [{"side_a": "tight", "side_b": "warm", "lean": "tight",
                  "reason": d[0]["reason"]}]
    assert rt.as_dicts("a plain request") == []
