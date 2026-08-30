"""Presets designed with the rig off, kept until it is on.

Most of this was already possible and nobody had joined it up. The planner
never touches the wire: it emits a plan in a closed action vocabulary, and a
plan is data. Validation runs against the registry, which is local files. The
grounding catalogs are local files. Exactly one line in the planning path
needs hardware, the one that reads current state for context.

What a design can never be is verified, because there is nothing to read back
from. So these pin the two halves: that it can be built and kept offline, and
that it never claims more than it has.
"""
import pytest
from fastapi.testclient import TestClient

import server
from fm9 import designs
from fm9.sim import SimFM9


@pytest.fixture(autouse=True)
def isolated_designs(tmp_path, monkeypatch):
    """Never write designs into the developer's own directory."""
    monkeypatch.setenv("TONECOMMAND_DESIGNS_DIR", str(tmp_path / "designs"))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    return TestClient(server.app)


ACTION = {"kind": "set_param", "block": "DISTORT", "instance": 1,
          "param": "DISTORT_MID", "value": 6.5, "reason": "scoop less"}


# --- a design is only kept once it would actually run ---------------------

def test_a_design_that_failed_validation_is_refused():
    """Saving a plan that cannot run would be storing a disappointment for
    later, and the browser is not the place that decides whether it runs."""
    with pytest.raises(ValueError, match="failed validation"):
        designs.save({"name": "bad", "actions": [
            {**ACTION, "validation_errors": ["unknown parameter"]}]})


def test_an_empty_design_is_refused():
    with pytest.raises(ValueError):
        designs.save({"name": "nothing", "actions": []})


def test_the_server_revalidates_rather_than_trusting_the_browser(client):
    r = client.post("/api/designs", json={
        "name": "nonsense",
        "actions": [{"kind": "set_param", "block": "DISTORT", "instance": 1,
                     "param": "NO_SUCH_PARAM", "value": 1}]})
    assert r.status_code == 400
    assert "validation" in r.json()["error"]


# --- reconnecting is a merge, not a hope ----------------------------------

def test_the_anchor_records_only_what_the_design_touches():
    """The whole buffer would be three thousand numbers to detect a change in
    one, and a preset moving somewhere this design does not edit is worth
    mentioning rather than blocking."""
    anchor = designs.anchor_for([ACTION], {"DISTORT_MID": 5.09, "DELAY_MIX": 24})
    assert anchor == {"DISTORT_MID": 5.09}


def test_a_rig_that_has_not_moved_is_clean():
    d = {"preset": {"number": 151}, "anchor": {"DISTORT_MID": 5.09}}
    assert designs.check(d, 151, {"DISTORT_MID": 5.09})["verdict"] == "clean"


def test_movement_elsewhere_is_not_a_conflict():
    d = {"preset": {"number": 151}, "anchor": {"DISTORT_MID": 5.09}}
    got = designs.check(d, 151, {"DISTORT_MID": 5.09, "DELAY_MIX": 99})
    assert got["verdict"] == "clean"


def test_movement_underneath_the_edit_is_a_conflict():
    """A queue that applied blindly would overwrite an edit made on the front
    panel in between, and the owner would have no way to know it had."""
    d = {"preset": {"number": 151}, "anchor": {"DISTORT_MID": 5.09}}
    got = designs.check(d, 151, {"DISTORT_MID": 8.0})
    assert got["verdict"] == "conflict"
    assert got["moved"][0] == {"param": "DISTORT_MID", "was": 5.09, "now": 8.0}


def test_rounding_is_not_mistaken_for_movement():
    """Display values are rounded to two places, so an exact float compare
    would report a conflict on every design."""
    d = {"preset": {"number": 151}, "anchor": {"DISTORT_MID": 5.09}}
    assert designs.check(d, 151, {"DISTORT_MID": 5.0901})["verdict"] == "clean"


def test_the_wrong_preset_is_caught_before_anything_else():
    d = {"preset": {"number": 151}, "anchor": {"DISTORT_MID": 5.09}}
    got = designs.check(d, 189, {"DISTORT_MID": 5.09})
    assert got["verdict"] == "wrong_preset"


# --- the shareable form ---------------------------------------------------

def test_a_recipe_carries_the_steps_and_not_the_author_s_rig():
    """A recipe is how to build a tone, never the tone file, and never a
    description of somebody else's unit."""
    rec = designs.to_recipe({
        "name": "Deftones lead", "summary": "darker", "actions": [ACTION],
        "anchor": {"DISTORT_MID": 5.09}, "preset": {"number": 151}})
    assert rec["recipe_version"] == 1
    assert rec["name"] == "deftones-lead"
    assert rec["steps"][0]["param"] == "DISTORT_MID"
    blob = str(rec)
    assert "anchor" not in blob and "5.09" not in blob
    assert "151" not in blob, "the author's slot number is not part of the tone"


# --- what it must never claim ---------------------------------------------

def test_the_page_says_a_design_is_not_verified():
    from pathlib import Path
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    assert "designed, not verified" in ui
    # and SEND checks before it proposes
    script = ui.split("<script>")[1]
    fn = script.split("async function sendDesign")[1].split("\n}\n")[0]
    assert "/check" in fn and fn.index("/check") < fn.index("showPlan(")


def test_sending_goes_through_the_same_confirm_gate():
    from pathlib import Path
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    script = ui.split("<script>")[1]
    fn = script.split("async function sendDesign")[1].split("\n}\n")[0]
    assert "showPlan(" in fn
    assert "/api/apply" not in fn, "a design must not transmit itself"
