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


# --- three kinds of context, in descending order of what is known ---------

def test_a_request_that_stands_on_its_own_plans_with_no_rig_at_all(monkeypatch):
    """Refusing outright was wrong, and Moncy found it with the right prompt:
    "give me a Steve Lukather lead tone in scene 4 of a new preset" is a build,
    not an edit. Every fact it needs is in the grounding catalogs.
    """
    import server
    from fastapi.testclient import TestClient
    from fm9.device import FM9NotFound
    monkeypatch.setattr(server, "_last_snapshot", {"state": None, "at": None})
    monkeypatch.setattr(server, "get_fm9",
                        lambda: (_ for _ in ()).throw(FM9NotFound("off")))
    monkeypatch.setattr(server, "drop_fm9", lambda: None)
    seen = {}
    monkeypatch.setattr(server.planner, "plan",
                        lambda prompt, state, ref: seen.update(state=state) or
                        {"summary": "ok", "clarification": None, "actions": []})
    d = TestClient(server.app).post("/api/plan", json={"prompt": "lukather lead"}).json()
    assert d["no_state"] is True and d["offline"] is True
    # and it is TOLD what it does and does not have, rather than handed an
    # empty state and left to plan a relative request against a zero. Asserts
    # the intent, not the wording: pinning the exact sentence broke the moment
    # the context was improved, which is the test's fault and not the code's.
    ctx = seen["state"].lower()
    assert "no preset has been read" in ctx
    assert "relative" in ctx and "refuse" in ctx


def test_a_profile_outranks_the_remembered_reading(monkeypatch):
    """You asked to design for someone else's rig, so designing for your own
    instead would be answering a different question."""
    import server
    from fastapi.testclient import TestClient
    from fm9 import rigprofile
    prof = {"profile_version": rigprofile.PROFILE_VERSION, "device": "FM9",
            "author": "brian", "preset_name": "Deftones base",
            "blocks": [{"family": "DISTORT", "instance": 1, "label": "Amp 1"}]}
    monkeypatch.setattr(server, "_profile", {"loaded": prof})
    monkeypatch.setattr(server, "_last_snapshot",
                        {"state": {"preset": {"name": "MINE"}, "scene": None,
                                   "blocks": [], "values": {}}, "at": "x"})
    seen = {}
    monkeypatch.setattr(server.planner, "plan",
                        lambda prompt, state, ref: seen.update(state=state) or
                        {"summary": "ok", "clarification": None, "actions": []})
    d = TestClient(server.app).post("/api/plan", json={"prompt": "x"}).json()
    assert "Deftones base" in seen["state"] and "MINE" not in seen["state"]
    assert d["profile"]["author"] == "brian"


# --- a profile is not a preset file -------------------------------------

def test_a_profile_carries_no_parameter_values():
    """A full dump of those IS the preset, and many presets on a real unit
    came from paid packs. docs/RECIPES.md: nothing paid is redistributed."""
    import json
    from fm9 import rigprofile
    snap = {"preset": {"number": 151, "name": "I Know A Name"},
            "scenes": [{"number": 1, "name": "CLEAN"}],
            "blocks": [{"family": "DISTORT", "instance": 1, "label": "Amp 1",
                        "bypassed": False, "channel": "A", "channels": 4}],
            "values": {"AMP_MODEL": "Brit 800", "cab": "4x12 Greenback",
                       "DISTORT_DRIVE": 7.2, "DELAY_MIX": 31.5}}
    blob = json.dumps(rigprofile.build(snap, author="monzta1"))
    assert "7.2" not in blob and "31.5" not in blob
    assert "DISTORT_DRIVE" not in blob
    # the slot number goes too: it locates the preset on ONE person's unit
    assert "151" not in blob
    # but which gear is emulated is a fact, the same class a recipe carries
    assert "Brit 800" in blob and "Greenback" in blob


def test_a_profile_from_a_future_version_is_refused():
    from fm9 import rigprofile
    assert rigprofile.check({"profile_version": 99, "device": "FM9",
                             "blocks": [1]})
    assert rigprofile.check({"profile_version": 1, "device": "Helix",
                             "blocks": [1]})
    assert rigprofile.check({"profile_version": 1, "device": "FM9",
                             "blocks": []})


def test_sending_a_design_built_for_another_rig_asks_first():
    """It names blocks from their preset and was never anchored to any value
    on yours, so there is nothing to check for drift."""
    from pathlib import Path
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    fn = ui.split("<script>")[1].split("async function sendDesign")[1].split("\n}\n")[0]
    assert "d.profile && !window.confirm" in fn
    assert "not yours" in fn


def test_the_blank_context_says_what_IS_true_not_only_what_is_missing():
    """Found by running the thing end to end.

    The first version listed only absences, so the planner reasonably
    concluded nothing was addressable and refused a perfectly answerable
    request: "build a Vox AC30 clean in scene 2, gain 3, bass 4.5" came back
    with zero actions and "there are no known blocks, scenes, or channels to
    act on". Every FM9 has scenes 1 to 8 and a fixed block vocabulary, and
    those are facts about the device rather than about one preset. Saying so
    turned the same prompt into eight actions with none blocked.
    """
    from fm9 import rigprofile
    text = rigprofile.as_blank_text().lower()
    assert "scenes 1 to 8" in text
    assert "addressable" in text
    # and an assumption stated out loud beats a refusal, because validation
    # and read-back catch a wrong one before anything is trusted
    assert "which ones you assumed" in text
    # while relative requests are still refused
    assert "refuse anything relative" in text
