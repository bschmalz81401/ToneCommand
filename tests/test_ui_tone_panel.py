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
    assert "click a block to bypass it, its letter to change channel" in UI
    assert "Drag any slider" in UI
    # and the one thing a player needs to know before touching either
    assert "edit buffer, not to your saved preset" in UI


# --- the signal chain is the actual grid ---
# It was a wrapped list of block names, which tells you what is in the preset
# but not how any of it is wired, and the wiring is the part that goes wrong:
# a severed cable and a bypassed Return both leave every block present and
# correct while the scene makes no sound.

def test_the_grid_endpoint_resolves_the_cables(client):
    """The browser is handed the source rows that feed each cell, not the
    bitmask they arrived in. How the cable mask is packed is a protocol fact
    and belongs on this side of the wire."""
    g = client.get("/api/grid").json()
    assert g["cells"] and "error" not in g
    for c in g["cells"]:
        assert isinstance(c["feeds"], list)
        assert all(isinstance(r, int) for r in c["feeds"])
        assert "live" in c


def test_the_walk_is_shared_with_the_audit_not_reimplemented():
    """Five silent-scene classes were found the hard way getting this
    traversal right. A second copy in the browser would drift from it."""
    from tools import path_audit
    assert hasattr(path_audit, "walk")
    alive, why = path_audit.scene_alive.__doc__, path_audit.walk.__doc__
    assert alive and why
    import inspect
    assert "walk(" in inspect.getsource(path_audit.scene_alive), \
        "scene_alive must delegate, so both answers come from one traversal"


def test_shunts_are_drawn_as_wire_not_as_blocks():
    """A shunt is a piece of cable. Drawing it as a box would imply the preset
    contains something it does not."""
    render = SCRIPT.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "if (c.shunt)" in render and "line class=\"shunt" in render.replace("`", "")


def test_a_cable_is_lit_only_when_both_ends_are():
    """A live block fed from a dead one is not being reached through THIS
    cable, and lighting it would draw a path the signal does not take."""
    render = SCRIPT.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "c.live && from && from.live" in render


def test_the_glow_filter_is_in_user_space():
    """objectBoundingBox is the default, and a horizontal line has a bounding
    box zero pixels tall, so a percentage filter region collapses and the
    element renders blank. Every straight wire in the chain was invisible
    while its computed stroke read as cyan."""
    assert 'filterUnits="userSpaceOnUse"' in SCRIPT


def test_empty_leading_rows_are_trimmed():
    """The device numbers rows on its own full grid. A preset using rows 1 to
    4 drawn at absolute coordinates wastes a row of panel on nothing."""
    render = SCRIPT.split("function renderGrid")[1].split("\nfunction ")[0]
    assert "Math.min(...g.cells.map(c => c.row))" in render


def test_bypassed_is_shown_by_dash_not_by_colour():
    """Colour here already means live versus dead. One signal must not carry
    two meanings, and a bypassed block still passes signal through."""
    assert "svg.grid .cell.byp > rect { stroke-dasharray" in UI


def test_a_dead_wire_is_still_legible():
    """A severed path is information. Drawn too dark it reads as empty space
    rather than as a fault."""
    assert "#22303a" not in UI, "the old near-invisible wire colour is back"


# --- auditioning amps and cabs ---
# The thing the device is worst at. On the FM9 you turn a knob through 1024
# cabinets one at a time because there is nowhere to type.

def test_the_rosters_are_served_whole(client):
    """Paging 2237 cabs would make the search feel like the unit's own list,
    which is the thing this is trying to beat."""
    amps = client.get("/api/models?kind=amp").json()
    cabs = client.get("/api/models?kind=cab").json()
    assert len(amps["banks"][0]["models"]) > 300
    assert sum(len(b["models"]) for b in cabs["banks"]) > 2000
    assert all("name" in b for b in cabs["banks"]), "banks must be named"


def test_a_cab_carries_its_description_for_searching(client):
    """"Vibrolux" is in the description, not in the name. Searching only names
    would miss the amp the cab was modelled on, which is how anyone actually
    looks for one."""
    cabs = client.get("/api/models?kind=cab").json()
    models = cabs["banks"][0]["models"]
    assert any(m.get("detail") for m in models)


def test_setting_a_cab_verifies_and_names_what_landed(client):
    c = client.get("/api/state").json()["cab_sel"]
    assert c and "bank" in c and "ordinal" in c
    r = client.post("/api/apply", json={"actions": [{
        "kind": "set_cab", "block": "CABINET", "instance": 1,
        "value": 200, "bank": 0}]}).json()
    res = r["results"][-1]
    assert res["ok"] and "cab ->" in res["detail"]


def test_a_cab_outside_the_bank_is_refused(client):
    r = client.post("/api/apply", json={"actions": [{
        "kind": "set_cab", "block": "CABINET", "instance": 1,
        "value": 99999, "bank": 0}]}).json()
    assert not r["results"][-1]["ok"]


def test_auditioning_goes_through_the_same_apply_path():
    """So it inherits undo, gig mode and read-back verification rather than
    reimplementing all three on a side channel."""
    load = SCRIPT.split("async function audLoad")[1].split("\n}")[0]
    assert "'/api/apply'" in load
    assert "refreshSnaps" in load, "an audition you cannot undo is a trap"


def test_stepping_is_on_the_arrow_keys():
    """The whole point: keep both hands on the guitar and step the shortlist
    without hunting for a button."""
    assert "ArrowDown" in SCRIPT and "ArrowUp" in SCRIPT
    assert "function audStep" in SCRIPT


def test_the_search_narrows_rather_than_widens():
    """Every word must match. An OR would make a second word return MORE
    results, which is the opposite of what typing more means."""
    assert "words.every(" in SCRIPT


def test_the_ordinal_is_accepted_directly():
    """The audition list already knows exactly which model it means and should
    not round trip through a name. Checked after the exact-name match so an
    amp actually called "59" still wins over ordinal 59."""
    import inspect
    src = inspect.getsource(server.resolve_type_ordinal)
    assert "needle.isdigit()" in src
    exact = src.index("str(label).lower() == needle")
    assert exact < src.index("needle.isdigit()")
