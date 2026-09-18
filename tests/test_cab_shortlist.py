"""Issue #82: never hand out a single cab. A family request gets meaningfully
different factory takes, and a plan whose set_cab has nothing from the
library to be compared against gets those takes as its listening set."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server


def test_v30_shortlist_is_diverse_and_on_the_rig():
    rows = server.factory_cab_shortlist("V30")
    assert 3 <= len(rows) <= 5
    assert len({r["base"] for r in rows}) >= 2, "one arbitrary pick is not a shortlist"
    assert all(r["on_rig"] and r["slot"]["bank"] == r["bank"]
               and r["slot"]["ordinal"] == r["ordinal"] for r in rows)
    assert all("v30" in r["name"].lower() for r in rows)
    banks = {r["bank"] for r in rows}
    assert banks, "the whole catalog is searched, not one bank"


def test_distinct_cabinets_come_before_more_mics_of_one():
    rows = server.factory_cab_shortlist("uber", limit=5)
    bases = [r["base"] for r in rows]
    # every distinct base appears before any base repeats
    first_repeat = next((i for i, b in enumerate(bases) if b in bases[:i]), len(bases))
    assert len(set(bases[:first_repeat])) == first_repeat
    assert len(set(bases)) >= 2


def test_base_and_mic_split_agrees_with_the_curated_roster_rule():
    assert server._cab_base_and_mic("4x12 UBER V30 (RW)") == ("4x12 UBER V30", "")
    assert server._cab_base_and_mic("4x12 FRACTAL V30 AT4047") == ("4x12 FRACTAL V30", "AT4047")
    # with the family word, the take is everything after it
    assert server._cab_base_and_mic("2x12o V30 107 Room_L CEL", "v30") == ("2x12o V30", "107 Room_L CEL")


def test_empty_or_unknown_query_is_an_answer_with_a_reason():
    assert server.factory_cab_shortlist("") == []
    assert server.factory_cab_shortlist("no such speaker xyz") == []
    c = TestClient(server.app)
    r = c.get("/api/cab/shortlist", params={"q": "no such speaker xyz"}).json()
    assert r["candidates"] == [] and "fewer than two" in r["why"]
    r = c.get("/api/cab/shortlist", params={"q": "V30"}).json()
    assert len(r["candidates"]) >= 3 and "why" not in r


def test_a_plan_with_a_set_cab_and_no_library_rows_gets_the_factory_set(monkeypatch):
    from fm9 import ir_service
    monkeypatch.setattr(ir_service, "enabled", lambda: False)
    result = {"actions": [{"kind": "set_cab", "block": "cab", "bank": 3,
                           "value": 40, "cab_name": "4x12 1960B V30 (RW)"}],
              "cab_need": "4x12 v30"}
    out = server.cab_listening_set(result, {"state": "unresolved"})
    cands = out["candidates"]
    assert len(cands) >= 2 and out["source"] == "factory"
    assert all(c["on_rig"] and c["slot"]["bank"] is not None for c in cands)
    assert any(c["chosen"] for c in cands), "the plan's own pick is in the set"
    assert out["why"] is None


def test_library_candidates_are_left_alone(monkeypatch):
    lib = {"candidates": [{"name": "lib.wav", "slot": None}], "why": None}
    monkeypatch.setattr(server, "_library_listening_set", lambda r, a, k=3: dict(lib))
    result = {"actions": [{"kind": "set_cab", "bank": 3, "value": 40}]}
    out = server.cab_listening_set(result, {})
    assert out["candidates"] == lib["candidates"] and "source" not in out


def test_no_set_cab_means_no_fallback(monkeypatch):
    from fm9 import ir_service
    monkeypatch.setattr(ir_service, "enabled", lambda: False)
    out = server.cab_listening_set({"actions": [{"kind": "set_param"}]}, {})
    assert out["candidates"] == [] and out["why"]


def test_a_cab_with_no_siblings_says_so(monkeypatch):
    from fm9 import ir_service
    monkeypatch.setattr(ir_service, "enabled", lambda: False)
    monkeypatch.setattr(server, "factory_cab_shortlist", lambda q, limit=5: [])
    result = {"actions": [{"kind": "set_cab", "bank": 3, "value": 40,
                           "cab_name": "4x12 ODDBALL"}]}
    out = server.cab_listening_set(result, {})
    assert out["candidates"] == [] and "nothing to audition" in out["why"]
