"""Bugs found by going looking, and the reasons they stay fixed.

None of these were reported. They are the failure paths of the modifier and
section work: what happens when the preset empties, when a read hiccups, when
the planner names an action the browser invented, and when a write lands on a
parameter that something else owns.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from fm9.sim import SimFM9

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
SCRIPT = UI.split("<script>")[1]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    monkeypatch.setattr(server, "_last_snapshot", {"state": None, "at": None})
    return TestClient(server.app)


# --- the page emptied except for the bits nothing cleared ------------------

def test_an_empty_reading_clears_the_eq_panel_too():
    """The graphic EQ is not one of the SECTIONS, so the loop that blanks the
    parameter columns walked straight past it. Its faders stayed drawn and
    geqKeys stayed pointing at them, which left FLATTEN ALL live over a page
    otherwise reading "awaiting link" - ten writes aimed at a preset that may
    no longer have a graphic EQ in it."""
    fn = SCRIPT.split("if (!Object.keys(meta).length) {")[1].split("return;")[0]
    assert "geqKeys = [];" in fn
    assert "$('geqpanel').style.display = 'none';" in fn


def test_an_empty_reading_clears_the_amp_and_cab_pickers():
    """Left behind, they named a preset that was no longer loaded, beside a
    panel saying the link was gone."""
    fn = SCRIPT.split("if (!Object.keys(meta).length) {")[1].split("return;")[0]
    assert "$('picks').innerHTML = '';" in fn


# --- a plan card that described the wrong thing ----------------------------

def test_an_unbind_has_its_own_wording():
    """It fell through to the set_param branch and rendered as "Mix: null",
    because an unbind carries no value. Reachable: the planner is allowed to
    propose one."""
    fn = SCRIPT.split("function describe(a)")[1].split("\n}\n")[0]
    assert "a.kind === 'unbind_pedal'" in fn
    assert fn.index("unbind_pedal") < fn.index("lastParams[a.param]")


def test_the_planner_can_propose_both_directions():
    """Binding without unbinding would mean a plan could create a binding that
    only the browser could remove."""
    planner = Path("fm9/planner.py").read_text()
    assert planner.count('"unbind_pedal"') >= 2, "schema and the filter"


# --- a convenience must not be able to take down the page ------------------

def test_a_failed_modifier_read_does_not_drop_the_link(client, monkeypatch):
    """/api/state turns ANY exception into drop_fm9() and a red link light.
    Knowing what drives a parameter is a convenience; losing the whole page
    and the port because one modifier read hiccuped is not a trade worth
    making."""
    monkeypatch.setattr(server, "read_modifiers",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json()["connected"] is True
    assert r.json()["mods"] == {}


def test_one_bad_slot_does_not_lose_the_other_thirty_one(client, monkeypatch):
    fm9 = server._fm9
    real = fm9.bulk_read

    def flaky(eid, *a, **k):
        if eid == 5:                      # one modifier slot, not the rest
            raise RuntimeError("no answer")
        return real(eid, *a, **k)

    monkeypatch.setattr(fm9, "bulk_read", flaky)
    with fm9:
        assert server.read_modifiers(fm9) == {}      # none bound, none crashed


# --- writing to a parameter something else owns ---------------------------

def test_setting_a_driven_parameter_is_warned_about(client):
    """It lands, it verifies by read-back, and nothing changes that anyone can
    hear, because the FM9 sources the value from the modifier. "Verified" on a
    change with no audible effect is the most misleading thing this tool can
    say. The browser refuses to draw the slider; a plan can still name it."""
    server._last_snapshot["state"] = {"mods": {"DELAY_MIX": {
        "slot": 3, "source": 11, "source_name": "Pedal 2", "known": True}}}
    errs, warns = server.validate_action(server.Action(
        kind="set_param", block="delay", instance=1, param="DELAY_MIX", value=30))
    assert not errs, "it is a warning, not a refusal: it is still their rig"
    assert any("driven by Pedal 2" in w and "no difference" in w for w in warns)


def test_an_undriven_parameter_is_not_warned_about(client):
    server._last_snapshot["state"] = {"mods": {}}
    _, warns = server.validate_action(server.Action(
        kind="set_param", block="delay", instance=1, param="DELAY_MIX", value=30))
    assert not any("driven by" in w for w in warns)


def test_the_warning_survives_never_having_read_the_rig(client):
    """Designing offline, there is no snapshot at all."""
    server._last_snapshot["state"] = None
    _, warns = server.validate_action(server.Action(
        kind="set_param", block="delay", instance=1, param="DELAY_MIX", value=30))
    assert not any("driven by" in w for w in warns)


def test_the_warning_says_how_fresh_it_is():
    """It comes from the last poll, not from a read taken now, and a claim
    about someone's rig has to carry its own age."""
    src = Path("server.py").read_text()
    fn = src.split("def validate_action(")[1].split("\ndef ")[0]
    assert "as of the last reading" in fn


# --- the flag that silences the whole panel -------------------------------

def test_a_row_without_a_key_cannot_freeze_the_panel():
    """`dragging` suppresses the entire parameter repaint. Both drag handlers
    used to read row.dataset BEFORE clearing it, so a row shape with no
    data-key would throw between setting the flag and clearing it, leaving it
    true for the rest of the session: the panel silently stops updating, with
    no error anyone would connect to the cause.

    Not hypothetical. The modifier-driven row IS a row shape with no data-key,
    one removed `disabled` attribute away from reaching these handlers.
    """
    inp = SCRIPT.split("box.addEventListener('input'")[1].split("\n}));")[0]
    assert inp.index("if (!row) return;") < inp.index("dragging = true"), \
        "the input handler must bail before it sets the flag"

    chg = SCRIPT.split("box.addEventListener('change'")[1].split("\n}));")[0]
    assert chg.index("dragging = false") < chg.index("closest('[data-key]')"), \
        "the change handler must clear the flag before it can throw"
    assert "if (!row) return;" in chg


def test_the_reset_button_survives_a_missing_row():
    fn = SCRIPT.split("closest('button.v.reset')")[1].split("\n}));")[0]
    assert "if (!row) return;" in fn


# --- one question, one rule -----------------------------------------------

def test_the_pedal_button_and_the_server_agree_on_what_is_bindable(client):
    """Two rules for one question drift. FUZZ_TYPE is the proof: a model
    selector whose unit is `unverified` rather than `enum`, which the server
    refuses as a selector and which the browser's rule, had it only looked for
    `enum`, would have offered a pedal button for."""
    fn = SCRIPT.split("function pedalable(key, m)")[1].split("\n}\n")[0]
    assert "m.unit === 'enum' || m.unit === 'unverified'" in fn

    state = client.get("/api/state").json()
    checked = 0
    for key, m in state["params"].items():
        offered = m["unit"] not in ("enum", "unverified") and (m["max"] - m["min"]) > 0
        errs, _ = server.validate_action(server.Action(
            kind="bind_pedal", block=m["family"], instance=m["instance"],
            param=m["param"]))
        assert offered == (not errs), (key, m["unit"], errs)
        checked += 1
    assert checked > 20, "not enough parameters to call this a check"
