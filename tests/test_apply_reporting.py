"""What the UI is told when an action is refused, and what it does with it.

Two faults found by the owner on v0.3.0 while asking for an amp on an empty
preset. Neither was a transmit failure, and one of them said it was.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from fm9.registry import Registry
from fm9.sim import SimFM9

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    return TestClient(server.app)


# --- the skipped marker is a contract, and the UI has to implement it ---

def test_a_refused_add_block_still_reports_a_null_action(client):
    """The server appends a marker with action None so later actions are not
    run against a block that never landed. Pinned here as well as in
    test_builder_actions, because the UI reads this shape."""
    body = {"actions": [
        {"kind": "add_block", "block": "amp", "instance": 1},
        {"kind": "add_block", "block": "amp", "instance": 1},
    ]}
    results = client.post("/api/apply", json=body).json()["results"]
    assert any(r["action"] is None for r in results)


def test_the_ui_guards_the_null_action_before_touching_kind():
    """`describe(res.action)` read .kind straight off the marker. The throw
    escaped the results loop, so the remaining results never logged and the
    log said

        transmit failed: Cannot read properties of null (reading 'kind')

    about a transmit that had happened and been reported correctly.

    Asserts a guard exists, not which one. The first version of this test
    demanded the exact line `if (!a) return 'skipped';`, and the independent
    review called that out as pinning tokens rather than behaviour: an equally
    correct guard would fail it. That prediction came true within hours, when
    upstream shipped `if (!a) return 'plan halted';` for the same fault."""
    body = UI.split("function describe(a) {")[1].split("\n}")[0]
    code = [ln.strip() for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith("//")]
    assert code, "describe() has no body"
    guard = code[0]
    assert "return" in guard and ("!a" in guard or "a ==" in guard
                                  or "a ===" in guard), (
        f"describe() must handle a null action before touching .kind, got {guard!r}")
    assert ".kind" not in guard


# --- a refusal names the wall it actually hit ---

def test_an_empty_preset_is_not_described_as_having_no_free_cell(client,
                                                                 monkeypatch):
    """An empty FM9 slot has no grid cells at all, not even shunts (finding
    18), so "no free pass-through cell" describes a packed preset and tells
    the owner of a blank one nothing they can act on."""
    monkeypatch.setattr(server._fm9, "read_grid", lambda: [])
    monkeypatch.setattr(server._fm9, "status_dump", lambda: [])
    results = client.post("/api/apply", json={
        "actions": [{"kind": "add_block", "block": "amp", "instance": 1}]}).json()["results"]
    detail = results[0]["detail"]
    assert results[0]["ok"] is False
    assert "this preset is empty" in detail
    assert "build_from_scratch" in detail, "say what the answer actually is"
    assert "no free pass-through cell" not in detail


def test_the_position_reads_as_a_phrase_not_an_enum():
    """It rendered as "no free pass-through cell any of the amp"."""
    from fm9.device import FM9  # noqa: F401  (Action import path)
    from server import Action, _no_placement_detail
    cells = [object()]                    # non-empty: the packed-preset branch
    a = Action(kind="add_block", block="amp", instance=1)
    for pos, phrase in (("pre", "before the amp"), ("post", "after the amp"),
                        ("any", "anywhere on the grid")):
        got = _no_placement_detail(a, pos, cells)
        assert phrase in got, got
        assert f"cell {pos} of the amp" not in got
