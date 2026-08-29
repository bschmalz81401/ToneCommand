"""The TONE panel: grouped, in the unit's own words, and adjustable.

It used to be AMP TELEMETRY, a wall of right-aligned numbers under a heading
no guitarist says, and it was a readout. The one question it invited, "so
nudge the mid up a bit", it could not answer, which is the whole complaint
behind issue #34: a panel you can only look at sends you to FM9-Edit, and a
tool you leave in the middle of a session is one you stop opening.

Three things had to be true to fix it, and these pin all three:

  the ranges are the registry's, not a table in the browser
  a value we cannot back up is not drawn at all
  one drag puts one write on the wire, not one per pixel
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
UI = (ROOT / "ui" / "index.html").read_text()
SCRIPT = UI.split("<script>")[1]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    return TestClient(server.app)


# --- ranges belong to the registry ---------------------------------------

def test_the_state_payload_describes_every_value_it_sends(client):
    """The browser used to carry its own table of maxima, so it could only
    draw the seven amp knobs it had heard of and drew each as though it ran
    0-10. Threshold runs -100 to 0 and delay time to 16000ms."""
    s = client.get("/api/state").json()
    assert s["params"], "no metadata, so the UI is back to guessing"
    for key, m in s["params"].items():
        assert key in s["values"], f"{key} described but not sent"
        for field in ("family", "instance", "param", "min", "max", "label"):
            assert field in m, f"{key} missing {field}"


def test_the_metadata_is_enough_to_write_the_value_back(client):
    """Every field set_param needs comes from the same payload that drew the
    slider, so the UI never has to reconstruct a block name."""
    s = client.get("/api/state").json()
    m = s["params"]["DISTORT_DRIVE"]
    r = client.post("/api/apply", json={"actions": [{
        "kind": "set_param", "block": m["family"], "instance": m["instance"],
        "param": m["param"], "value": (m["min"] + m["max"]) / 2}]}).json()
    assert r["results"][0]["ok"], r


def test_labels_are_the_ones_the_unit_uses(client):
    """DISTORT_DRIVE is our name for it. The amp calls it Gain."""
    p = client.get("/api/state").json()["params"]
    assert p["DISTORT_DRIVE"]["label"] == "Gain"
    assert "_" not in p["DISTORT_DRIVE"]["label"]


def test_the_browser_keeps_no_table_of_its_own():
    assert "KNOB_LABELS" not in SCRIPT, \
        "ranges hardcoded in the browser drift from the registry silently"


# --- a number we cannot back up is not drawn -----------------------------

def test_parameters_with_an_unverified_range_are_left_out():
    """FUZZ_TYPE is a model selector whose ordinal arrives scaled to 0.08. A
    row reading TYPE 0.08 states nothing true, and it would sit there looking
    exactly as authoritative as Gain 1.2."""
    assert "unit !== 'unverified'" in SCRIPT


def test_a_parameter_with_no_span_stays_a_readout():
    """Not every missing range is worth hiding, but none of them is worth a
    slider: a control we cannot map back would look as trustworthy as one we
    can."""
    assert "if (!(span > 0))" in SCRIPT
    assert "read only" in SCRIPT


# --- one drag, one write --------------------------------------------------

def test_the_write_happens_on_change_not_on_input():
    """input fires for every pixel of travel. Writing there would put a SysEx
    message on the wire for each one, which is how a MIDI port is flooded."""
    change = SCRIPT.split("$('knobs').addEventListener('change'")[1].split("\n});")[0]
    inp = SCRIPT.split("$('knobs').addEventListener('input'")[1].split("\n});")[0]
    assert "set_param" in change, "nothing is transmitted when the drag ends"
    assert "set_param" not in inp and "fetch" not in inp, \
        "the input handler must only move the track fill"


def test_the_poll_does_not_repaint_under_a_moving_thumb():
    """State refreshes every five seconds. Re-rendering mid-drag would replace
    the element being dragged and drop the gesture."""
    assert "if (!dragging) renderParams(s)" in SCRIPT
    inp = SCRIPT.split("$('knobs').addEventListener('input'")[1].split("\n});")[0]
    assert "dragging = true" in inp


def test_the_control_is_a_real_range_input():
    """Draggable, keyboard reachable and screen-reader labelled without
    reinventing any of it on a div and a mousemove handler."""
    assert 'type="range"' in SCRIPT
    assert "aria-label" in SCRIPT.split("function knobRow")[1].split("\n}")[0]


# --- what the panel leads with -------------------------------------------

def test_the_amp_model_and_cab_are_shown(client):
    """Both were read on every poll and then thrown away. They are the two
    facts a player leads with, and neither appeared anywhere on the page."""
    s = client.get("/api/state").json()
    assert "AMP_MODEL" in s["values"]
    render = SCRIPT.split("function renderParams")[1].split("\nfunction ")[0]
    assert "AMP_MODEL" in render and "values.cab" in render


def test_the_panel_is_grouped_by_block():
    """A flat list of thirty values is a wall. The question always arrives
    attached to a block: the delay a little wetter, the gate tighter."""
    assert "PARAM_GROUPS" in SCRIPT
    groups = SCRIPT.split("const PARAM_GROUPS = [")[1].split("];")[0]
    for fam in ("DISTORT", "INPUT", "DELAY", "REVERB"):
        assert f"'{fam}'" in groups


def test_it_is_not_called_telemetry():
    # the heading itself, not the comment that records why it changed
    assert 'data-label="AMP TELEMETRY"' not in UI
    assert 'data-label="TONE"' in UI


def test_both_control_surfaces_say_what_they_do():
    """Nothing else on the page announces itself, and a control that looks
    like a readout is a control nobody finds."""
    assert "Click a block to bypass or engage it" in UI
    assert "Drag any slider" in UI
    # and the one thing a player needs to know before touching either
    assert "edit buffer, not to your saved preset" in UI
