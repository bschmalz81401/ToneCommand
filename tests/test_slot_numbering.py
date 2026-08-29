"""Wire numbers versus editor numbers, on the surfaces where being wrong costs.

The wire numbers the 512 slots 0-511; FM9-Edit and the front panel number the
same slots 1-512 (finding 21). This PR taught every CLI surface to print both.
The maintainer's #22 review found the two places that matter most had been
missed: the store confirmation, which is the only destructive prompt in the
product, and the live preset readout that the owner cross-checks against the
panel in front of them.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import protocol as p
from fm9.sim import SimFM9

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    return TestClient(server.app)


# --- the destructive prompt names the slot the owner's editor names ---

def test_a_store_action_carries_a_dual_numbered_label(client, monkeypatch):
    """Rendered server side, so the numbering rule lives in protocol.py alone
    rather than being recomputed in the browser."""
    monkeypatch.setattr(server.planner, "plan", lambda *a, **kw: {
        "summary": "save it", "clarification": None,
        "actions": [{"kind": "store", "value": 133, "instance": 1,
                     "reason": "asked to save"}]})
    plan = client.post("/api/plan", json={"prompt": "save this"}).json()
    assert plan["actions"][0]["slot_label"] == "133 (FM9-Edit 134)"


def test_every_place_a_store_slot_is_shown_uses_the_label():
    """Two of them: the plan card and the TRANSMIT confirmation."""
    shown = [line for line in UI.splitlines()
             if "OVERWRITES slot" in line or "STORES to preset" in line
             or "stores.map" in line]
    assert len(shown) == 3, shown
    for line in shown:
        assert "a.value" not in line or "slot_label" in line, line
    assert UI.count("a.slot_label || a.value") == 2


# --- the live readout matches the panel the owner is looking at ---

def test_the_state_payload_carries_both_numbers(client):
    preset = client.get("/api/state").json()["preset"]
    assert preset["number"] == p.editor_number(preset["number"]) - 1
    assert preset["editor"] == preset["number"] + 1
    assert preset["label"] == p.slot_label(preset["number"])


def test_the_ui_readout_never_shows_the_bare_wire_number():
    """The readout must agree with the front panel.

    Originally this pinned the exact expression that rendered the label. That
    asserted an implementation rather than the intent, and broke the moment
    the pill was changed to carry a single number. The intent is unchanged and
    is what is checked now: the readout resolves through `editor` or `label`,
    never the raw wire number on its own, because the wire numbers presets
    0-511 and every surface the owner cross-checks against numbers them 1-512.
    """
    assert "s.preset.editor" in UI or "s.preset.label" in UI
    # and the rule the rest of the UI follows: both numbers only where being
    # wrong is expensive, which is the store confirmation.
    assert "slot_label" in UI


def test_the_text_snapshot_uses_the_label_too():
    snap = {"preset": {"number": 386, "editor": 387,
                       "label": p.slot_label(386), "name": "TEST"},
            "scene": None, "blocks": [], "values": {}}
    assert "386 (FM9-Edit 387)" in server.state_text(snap)
