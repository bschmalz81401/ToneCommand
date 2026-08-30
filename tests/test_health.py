"""Preset health: the check FM9-Edit structurally cannot offer.

FM9-Edit edits presets. It does not reason about them, so it will happily let
you save a preset whose scene 4 makes no sound and say nothing. These checks
have existed here for weeks as command-line scripts, which is to say nobody
outside this repo has ever run one.

The clone check earns its place on evidence. Preset 151 scene 4 was a copy of
scene 3, path_audit, audit_scenes and level_report all passed it, and Moncy
found it by ear. When the check was finally written it flagged a THIRD scene
those audits had also passed: scene 6, named PITCH, identical to CRUNCH.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import health
from fm9.sim import SimFM9


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    return TestClient(server.app)


# --- a scan is audible, so it is never something that just happens ---------

def test_scanning_is_a_post():
    """Not a GET. A GET can be prefetched by a browser, replayed by a refresh
    or followed by a crawler, and this one walks the rig through every scene
    out loud. The verb has to match the side effect."""
    routes = {(r.path, tuple(sorted(r.methods))) for r in server.app.routes
              if getattr(r, "methods", None)}
    assert ("/api/health", ("POST",)) in routes


def test_nothing_scans_on_a_timer():
    """The mistake that nearly shipped in shared_scenes(), which would have
    cycled the rig audibly every five seconds on the state poll."""
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    script = ui.split("<script>")[1]
    refresh = script.split("async function refresh()")[1].split("\n}")[0]
    assert "/api/health" not in refresh and "scanPreset" not in refresh
    assert "$('scan').onclick" in script, "it must take a deliberate click"


def test_gig_mode_refuses_to_scan(client):
    """A scan makes noise for several seconds, which on stage is precisely the
    thing gig mode exists to prevent."""
    client.post("/api/gig", json={"on": True})
    try:
        r = client.post("/api/health")
        assert r.status_code == 423
        assert "GIG MODE" in r.json()["error"]
    finally:
        client.post("/api/gig", json={"on": False})


def test_the_scan_puts_the_scene_back(client):
    """Reading scene 4 means standing in scene 4. Whoever asked for a scan did
    not ask to be moved."""
    fm9 = server._fm9
    with fm9:
        fm9.set_scene(3)
        before = fm9.scene_name()[0]
        health.scan(fm9, server.reg)
        assert fm9.scene_name()[0] == before


def test_the_scene_is_restored_even_when_a_read_raises(client, monkeypatch):
    fm9 = server._fm9
    with fm9:
        fm9.set_scene(2)
        monkeypatch.setattr(fm9, "status_dump",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        with pytest.raises(RuntimeError):
            health.scan(fm9, server.reg)
        assert fm9.scene_name()[0] == 2


# --- the same scene twice --------------------------------------------------

def test_identical_scenes_are_reported_as_one_finding():
    """Grouped, not pairwise. Four identical scenes are one problem, and six
    findings about it would bury every other check."""
    scenes = [{"number": n, "name": f"S{n}", "alive": True, "why": "",
               "amp_db": None, "vol": None, "unread": [],
               "_print": ("same",) if n in (3, 4, 6) else (f"u{n}",)}
              for n in range(1, 9)]
    found = [f for f in health._cross_scene(scenes, 3.0, 6.0)
             if f["kind"] == "clone"]
    assert len(found) == 1
    assert found[0]["scenes"] == [3, 4, 6]


def test_a_fingerprint_is_bypass_and_channel_only():
    """FM9 parameters live on the CHANNEL, not on the scene. What a scene
    stores is which blocks are bypassed and which channel each is on, so two
    scenes agreeing on those are the same scene, necessarily, without reading
    a single parameter. Same fact the blast-radius warning is built on, used
    in the other direction."""
    class B:
        def __init__(s, e, byp, ch):
            s.effect_id, s.bypassed, s.channel = e, byp, ch
    a = health._fingerprint([B(58, False, 0), B(102, True, 1)])
    b = health._fingerprint([B(102, True, 1), B(58, False, 0)])
    assert a == b, "order of the status dump must not matter"
    assert a != health._fingerprint([B(58, False, 1), B(102, True, 1)])


def test_unnamed_scenes_are_not_scenes():
    """A slot the owner never named is spare capacity, not a duplicate."""
    assert health._blank("") and health._blank("-") and health._blank(None)
    assert not health._blank("CRUNCH")


# --- a blank must never mean two different things --------------------------

def test_a_failed_read_is_not_reported_as_an_empty_cell():
    """Observed on 151: one blank volume gain in a scan whose other seven
    scenes read fine, and three re-reads of that scene returned the value
    every time. Drawn as a blank it reads as "this scene has no volume block",
    which is a false statement about the rig."""
    scenes = [{"number": 1, "name": "S1", "alive": True, "why": "",
               "amp_db": None, "vol": None, "unread": ["vol"], "_print": ("a",)}]
    found = [f for f in health._cross_scene(scenes, 3.0, 6.0)
             if f["kind"] == "incomplete"]
    assert len(found) == 1
    assert "unknown, not empty" in found[0]["detail"]


def test_a_bypassed_block_reads_as_no_level_not_as_a_failure():
    class B:
        effect_id, bypassed, channel = 102, True, 0
    val, why = health._read(None, server.reg, {102: B()}, server.reg.spec("VOLUME", 0), 102)
    assert val is None and why == "bypassed"


def test_reads_are_retried_before_being_called_unread():
    """A read that does not come back once usually comes back next time."""
    assert health.READ_TRIES >= 2
    calls = {"n": 0}

    class Dev:
        def get_param_wire(self, spec, channel=0):
            calls["n"] += 1
            return 52427 if calls["n"] >= 2 else None

    class B:
        effect_id, bypassed, channel = 102, False, 0
    val, why = health._read(Dev(), server.reg, {102: B()},
                            server.reg.spec("VOLUME", 0), 102)
    assert why == "ok" and val is not None
    assert calls["n"] == 2


# --- the honest bottom rung ------------------------------------------------

def test_the_ladder_never_claims_the_tone_is_good(client):
    """Every check here is a machine reading a wire. None of them can hear."""
    d = client.post("/api/health").json()
    assert d["ears"] == "pending"


def test_levels_are_reported_side_by_side_and_never_summed(client):
    """Loudness is not one number. On 151 the amp level is flat across all
    eight scenes while the volume gain climbs 7.0 to 9.0, so a scan reading
    only the amp would have reported even levels about a deliberate
    staircase."""
    d = client.post("/api/health").json()
    for s in d["scenes"]:
        assert "amp_db" in s and "vol" in s
    assert not any(k in s for s in d["scenes"] for k in ("level", "loudness"))


# --- a finding can propose its own repair ---

def test_a_level_finding_states_the_exact_change():
    """Arithmetic, not taste. The target came from the scan that just ran, so
    it is stated in the action vocabulary rather than described to a language
    model and hoped for."""
    scenes = [{"number": n, "name": f"S{n}", "alive": True, "why": "",
               "amp_db": (-18 if n != 2 else -14), "vol": 10, "unread": [],
               "_print": (f"u{n}",)} for n in range(1, 9)]
    hot = [f for f in health._cross_scene(scenes, 3.0, 6.0) if f["kind"] == "hot"]
    assert hot and hot[0]["fix"]["how"] == "actions"
    kinds = [a["kind"] for a in hot[0]["fix"]["actions"]]
    # the scene first: amp level lives on the CHANNEL, so you have to be
    # standing on the scene before the write means anything
    assert kinds == ["set_scene", "set_param"]
    assert hot[0]["fix"]["actions"][1]["value"] == -18


def test_a_clone_asks_the_planner_rather_than_guessing():
    """Making a scene its own sound is taste. What it must not do is invent
    numbers, so it hands over a grounded sentence instead."""
    scenes = [{"number": n, "name": f"S{n}", "alive": True, "why": "",
               "amp_db": None, "vol": None, "unread": [],
               "_print": ("same",) if n in (3, 4) else (f"u{n}",)}
              for n in range(1, 9)]
    clone = [f for f in health._cross_scene(scenes, 3.0, 6.0)
             if f["kind"] == "clone"][0]
    assert clone["fix"]["how"] == "prompt"
    assert "Do not add blocks" in clone["fix"]["prompt"]


def test_a_wide_spread_is_offered_no_fix():
    """The scene 1-5 loudness staircase is a convention here. Offering to
    flatten it would be offering to undo the thing the preset was built for."""
    scenes = [{"number": n, "name": f"S{n}", "alive": True, "why": "",
               "amp_db": -30 + n * 3, "vol": 10, "unread": [], "_print": (f"u{n}",)}
              for n in range(1, 9)]
    spread = [f for f in health._cross_scene(scenes, 3.0, 6.0)
              if f["kind"] == "spread"]
    assert spread and spread[0]["fix"] is None


def test_the_fix_proposes_and_never_transmits():
    """The whole safety model depends on this: a fix fills the plan box and
    stops, so the confirm gate, validation, the blast radius warning and the
    undo snapshot all still stand in front of it."""
    from pathlib import Path
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    script = ui.split("<script>")[1]
    fn = script.split("async function fixAll()")[1].split("\n}\n")[0]
    assert "showPlan(" in fn
    assert "/api/apply" not in fn, "a fix must never write"
    # and one button for the report, not one per finding
    assert "id=\"fixall\"" in script and "data-fix=" not in script


def test_fixed_is_measured_not_claimed():
    """The scan runs again by itself once a fix has actually landed."""
    from pathlib import Path
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    script = ui.split("<script>")[1]
    assert "fixPending = true" in script
    apply_fn = script.split("async function apply()")[1].split("\n}\n")[0]
    assert "scanPreset()" in apply_fn
