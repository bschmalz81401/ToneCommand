"""#137 and #138: the IRCommand seam on the ToneCommand side.

IRCommand is stubbed at the real boundary (fm9.ir_service.recommend and
measured_check) and the slot link at user_cabs.slot_for_source, so these
prove what ToneCommand does with what the library says, not the library.
"""
import sys

sys.argv = ["x"]
import pytest

import server
from fm9 import ir_service, user_cabs


def _rows():
    """Four library candidates in IRCommand's order: B and D are on the rig."""
    return [
        {"name": "A-disk.wav", "path": "/lib/A.wav", "pack": "P", "match": 0.9, "why": ["V30"],
         "measured": {"low": -10.0, "mid": -6.0, "presence": -8.0, "fizz": -14.0, "brightness": 2400.0},
         "distance_from_reference": 1.1},
        {"name": "B-rig.wav", "path": "/lib/B.wav", "pack": "P", "match": 0.8, "why": ["V30"],
         "measured": {"low": -14.0, "mid": -6.0, "presence": -5.0, "fizz": -14.0, "brightness": 2400.0},
         "distance_from_reference": 2.0},
        {"name": "C-disk.wav", "path": "/lib/C.wav", "pack": "P", "match": 0.7, "why": ["V30"],
         "measured": None, "distance_from_reference": None},
        {"name": "D-rig.wav", "path": "/lib/D.wav", "pack": "P", "match": 0.6, "why": ["V30"],
         "measured": {"low": -9.5, "mid": -6.4, "presence": -8.0, "fizz": -16.0, "brightness": 2700.0},
         "distance_from_reference": 3.3},
    ]


CURRENT = {"low": -10.0, "mid": -6.0, "presence": -8.0, "fizz": -14.0, "brightness": 2400.0}


def _stub(monkeypatch, capture, *, answer=None, measured=True, hints=None, hints_key=True):
    monkeypatch.setattr(ir_service, "enabled", lambda: True)

    def fake_recommend(need, target="fm9", k=3, reference=None, preserve=None, preserve_when=None, detail=None, **kw):
        capture.update(need=need, reference=reference, kw=dict(kw))
        if detail is not None:
            detail["preserve_asked"] = False
            if kw and hints_key:
                detail["hints_applied"] = hints if hints is not None else [
                    {"feature": "presence", "direction": 1, "sources": [kw.get("role")]} if kw.get("role") else None,
                    {"feature": "low", "direction": -1, "sources": [kw.get("tuning")]} if kw.get("tuning") else None]
                detail["hints_applied"] = [h for h in detail["hints_applied"] if h]
            elif kw and not hints_key:
                detail["hints_undecided"] = True
        return list(answer if answer is not None else _rows())

    def fake_measured(path):
        capture["measured_asked"] = path
        return {"path": path, "measured": measured, "features": CURRENT if measured else None}

    monkeypatch.setattr(ir_service, "recommend", fake_recommend)
    monkeypatch.setattr(ir_service, "measured_check", fake_measured)
    monkeypatch.setattr(user_cabs, "slot_for_source",
                        lambda path: (server.USER_CAB_BANK, 3) if path == "/lib/B.wav" else ((server.USER_CAB_BANK, 7) if path == "/lib/D.wav" else None))


PLAN = {"summary": "vai lead", "cab_need": "4x12 celestion v30 bright lead", "actions": [], "request": "a bright lead"}
MEASURED_ANCHOR = {"state": "measured", "reference": "/lib/current.wav", "name": "My V30", "bank": server.USER_CAB_BANK, "ordinal": 1}


# -- #137 on_rig first, deltas from a measured Current ----------------------------

def test_on_rig_candidates_come_first_in_ircommands_order(monkeypatch):
    seen = {}
    _stub(monkeypatch, seen)
    out = server.cab_listening_set(PLAN, MEASURED_ANCHOR, k=4)
    names = [c["name"] for c in out["candidates"]]
    assert names == ["B-rig.wav", "D-rig.wav", "A-disk.wav", "C-disk.wav"]
    assert [c["on_rig"] for c in out["candidates"]] == [True, True, False, False]
    assert out["candidates"][0]["slot"] == {"bank": server.USER_CAB_BANK, "ordinal": 3, "label": server.cab_label(server.USER_CAB_BANK, 3)}
    assert out["candidates"][2]["slot"] is None
    # nothing re-scored or dropped
    assert [c["match"] for c in out["candidates"]] == [0.8, 0.6, 0.9, 0.7]


def test_on_rig_deltas_carry_signs_and_words_from_a_measured_current(monkeypatch):
    seen = {}
    _stub(monkeypatch, seen)
    out = server.cab_listening_set(PLAN, MEASURED_ANCHOR, k=4)
    assert seen["measured_asked"] == "/lib/current.wav"
    by = {c["name"]: c for c in out["candidates"]}
    assert by["B-rig.wav"]["axes"] == {"low": -4.0, "mid": 0.0, "presence": 3.0, "fizz": 0.0, "brightness": 0.0}
    assert by["B-rig.wav"]["axes_words"] == ["tighter low", "more presence"]
    assert by["D-rig.wav"]["axes"] == {"low": 0.5, "mid": -0.4, "presence": 0.0, "fizz": -2.0, "brightness": 300.0}
    assert by["D-rig.wav"]["axes_words"] == ["less fizz", "brighter"]  # 0.5 dB and 0.4 dB moves say nothing
    assert by["A-disk.wav"]["axes_words"] == [] and by["A-disk.wav"]["distance_from_current"] == 1.1
    assert by["C-disk.wav"]["axes"] is None and by["C-disk.wav"]["axes_words"] == []  # unmeasured candidate


def test_on_rig_no_axes_and_no_distance_without_a_measured_current(monkeypatch):
    for anchor in ({"state": "gear_anchored", "gear": "4x12 V30", "name": "4x12 RECTO", "bank": 3, "ordinal": 42},
                   {"state": "unresolved"}):
        seen = {}
        _stub(monkeypatch, seen)
        out = server.cab_listening_set(PLAN, anchor, k=4)
        assert "measured_asked" not in seen
        assert all(c["axes"] is None and c["axes_words"] == [] and c["distance_from_current"] is None for c in out["candidates"])
        assert [c["on_rig"] for c in out["candidates"]] == [True, True, False, False]


def test_on_rig_factory_fallback_rows_are_unchanged(monkeypatch):
    seen = {}
    _stub(monkeypatch, seen, answer=[])
    plan = dict(PLAN, actions=[{"kind": "set_cab", "value": 1, "bank": 3, "cab_name": "V30"}])
    out = server.cab_listening_set(plan, {"state": "unresolved"})
    # the #82 shape exactly as before: no path, on_rig True, no axes keys added
    assert out["candidates"] and all(r["on_rig"] is True and r["path"] is None and "axes" not in r and "axes_words" not in r
                                     for r in out["candidates"])


# -- #138 hints on the wire and in the note ----------------------------------------

def test_hints_go_on_the_wire_only_when_known(monkeypatch):
    seen = {}
    _stub(monkeypatch, seen)
    out = server.cab_listening_set(PLAN, MEASURED_ANCHOR, role="lead", tuning="drop c")
    assert seen["kw"] == {"role": "lead", "tuning": "drop c"}
    assert out["hints"] == {"role": "lead", "tuning": "drop c",
                            "applied": [{"feature": "presence", "direction": 1, "sources": ["lead"]},
                                        {"feature": "low", "direction": -1, "sources": ["drop c"]}],
                            "ignored": [], "undecided": False}
    seen = {}
    _stub(monkeypatch, seen)
    out = server.cab_listening_set(PLAN, MEASURED_ANCHOR, role=None, tuning="eb")
    assert seen["kw"] == {"tuning": "eb"} and out["hints"]["role"] is None
    seen = {}
    _stub(monkeypatch, seen)
    out = server.cab_listening_set(PLAN, MEASURED_ANCHOR)
    assert seen["kw"] == {} and out["hints"] == {"role": None, "tuning": None, "applied": [], "ignored": [], "undecided": False}


def test_hints_undecided_when_an_older_library_build_says_nothing(monkeypatch):
    seen = {}
    _stub(monkeypatch, seen, hints_key=False)
    out = server.cab_listening_set(PLAN, MEASURED_ANCHOR, role="lead")
    assert out["hints"]["undecided"] is True and out["hints"]["applied"] == []


def test_hints_ir_service_sends_role_and_tuning_only_when_given(monkeypatch):
    seen = {}
    monkeypatch.setattr(ir_service, "enabled", lambda: True)
    monkeypatch.setattr(ir_service, "_get", lambda q, timeout=3: seen.setdefault("q", q) and {"results": [], "hints_applied": [{"feature": "low", "direction": -1, "sources": ["drop c"]}]})
    detail = {}
    ir_service.recommend("mesa v30", "fm9", 3, detail=detail, role="lead", tuning="drop c")
    assert "&role=lead" in seen["q"] and "&tuning=drop%20c" in seen["q"]
    assert detail["hints_applied"][0]["sources"] == ["drop c"] and "hints_undecided" not in detail
    seen.clear()
    detail = {}
    monkeypatch.setattr(ir_service, "_get", lambda q, timeout=3: seen.setdefault("q", q) and {"results": []})
    ir_service.recommend("mesa v30", "fm9", 3, detail=detail)
    assert "role=" not in seen["q"] and "tuning=" not in seen["q"] and "hints_undecided" not in detail
    seen.clear()
    detail = {}
    ir_service.recommend("mesa v30", "fm9", 3, detail=detail, role="lead")
    assert "&role=lead" in seen["q"] and "tuning=" not in seen["q"] and detail["hints_undecided"] is True


def test_hints_scene_role_and_tuning_derivation():
    snap = {"scene": {"number": 4, "name": "Lead Boost"}, "preset": {"name": "P"}}
    assert server.scene_hints(snap, "make it sing in drop C", False) == ("lead", "drop c")
    assert server.scene_hints(snap, "make it sing", True) == (None, None)  # whole rig: every scene, no role
    assert server.scene_hints({"scene": {"number": 1, "name": "Sparkle"}}, "brighter", False) == (None, None)
    assert server.scene_hints(None, "eb standard crunch", False) == (None, "eb")
    assert server.scene_hints({"scene": {"number": 3, "name": "Crunch Rhythm"}}, "", False) == ("rhythm", None)


def test_hints_ui_note_and_rows_are_wired():
    html = open("ui/index.html", encoding="utf-8").read()
    assert "function cabHintNote(sel)" in html and "Ranked for ${parts.join(' in ')}" in html
    assert "older build that ignores the scene and tuning hints" in html
    assert "ON YOUR RIG &middot; audition in your own amp path" in html
    assert "IN YOUR LIBRARY &middot; load it with Cab-Lab first" in html
    # the slot branch comes before the playable branch in cabRow
    row = html[html.index("function cabRow("):html.index("// Issues #82/#83: the audition session")]
    assert row.index("${r.slot\n      ? `<button class=\"cabhear") < row.index("${playable\n      ? `<button class=\"cabplay")
    assert "if (r.axes_words && r.axes_words.length) bits.push(r.axes_words.join(', '));" in row
