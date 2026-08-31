"""A block that is switched off must not look like one that is on.

Moncy: "changing the drive pedal has no effect". It had none, and everything
was working correctly: the Drive block on the loaded preset was bypassed, so
it was not in the signal. The write lands, verifies by read-back, and you hear
nothing.

The signal chain has always drawn bypassed blocks dashed. The parameter panels
did not know about bypass at all, so the same block got a full set of
live-looking sliders one panel further down. Two views of one rig disagreeing
about whether a block is on is worse than either view alone.

Same failure as a modifier-driven parameter, different cause, so it gets the
same treatment: say so on the row, and warn the planner.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from fm9.sim import SimFM9

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
SCRIPT = UI.split("<script>")[1]
STYLE = UI.split("<style>")[1].split("</style>")[0]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    monkeypatch.setattr(server, "_last_snapshot", {"state": None, "at": None})
    return TestClient(server.app)


# --- the browser -----------------------------------------------------------

def test_the_panel_reads_the_block_state_it_was_already_being_sent():
    """s.blocks carries `bypassed` and the tone panel simply never looked."""
    render = SCRIPT.split("function renderParams")[1].split("\nfunction ")[0]
    assert "lastBypass = {};" in render
    assert "for (const b of (s.blocks || []))" in render
    assert "lastBypass[`${b.family}:${b.instance}`] = !!b.bypassed;" in render


def test_a_bypassed_group_says_so_and_is_dimmed():
    render = SCRIPT.split("function renderParams")[1].split("\nfunction ")[0]
    assert "off ? bypassBadge(fam, inst, title) : ''" in render
    assert "kgroup${off ? ' bypassed' : ''}" in render
    assert re.search(r"^\s*\.kgroup\.bypassed input\[type=range\] \{", STYLE, re.M)
    assert re.search(r"^\s*\.bypbadge \{", STYLE, re.M)


def test_the_sliders_stay_live():
    """Unlike a modifier-driven row. Dialling a block in before engaging it is
    ordinary work, and disabling the controls would forbid it. What was
    missing was any way to KNOW, not the ability to edit."""
    fn = SCRIPT.split("function bypassBadge(fam, inst, title)")[1].split("\n}\n")[0]
    assert "disabled" not in fn
    assert "nothing here is audible yet" in fn


def test_the_badge_is_also_the_fix():
    """"Engage it" is the next thing anyone wants, and hunting for the block
    in the signal chain to do it is a detour."""
    fn = SCRIPT.split("closest('button[data-engage]')")[1].split("\n}));")[0]
    assert "kind: 'set_bypass'" in fn and "bypassed: false" in fn
    assert "blockAction(" in fn and "fetch(" not in fn


def test_the_graphic_eq_gets_it_too():
    """It is a block like any other; it just happens to live in its own
    panel, which is exactly how it would have been missed."""
    assert 'id="geqbadge"' in UI
    assert "renderGeq(bands, meta, s.values, rest, off ? bypassBadge(" in SCRIPT
    fn = SCRIPT.split("function renderGeq(keys, meta, values, rest, badge)")[1]
    assert "$('geqbadge').innerHTML = badge || '';" in fn


# --- the planner path ------------------------------------------------------

def test_setting_a_bypassed_block_is_warned_about(client):
    server._last_snapshot["state"] = {
        "blocks": [{"family": "FUZZ", "instance": 1, "bypassed": True}], "mods": {}}
    errs, warns = server.validate_action(server.Action(
        kind="set_param", block="drive", instance=1, param="FUZZ_DRIVE", value=5))
    assert not errs, "it is a warning: dialling in a bypassed block is allowed"
    assert any("BYPASSED" in w and "hear no difference" in w for w in warns)


def test_an_engaged_block_is_not_warned_about(client):
    server._last_snapshot["state"] = {
        "blocks": [{"family": "FUZZ", "instance": 1, "bypassed": False}], "mods": {}}
    _, warns = server.validate_action(server.Action(
        kind="set_param", block="drive", instance=1, param="FUZZ_DRIVE", value=5))
    assert not any("BYPASSED" in w for w in warns)


def test_the_right_instance_is_checked(client):
    """Drive 1 bypassed says nothing about Drive 2."""
    server._last_snapshot["state"] = {"blocks": [
        {"family": "FUZZ", "instance": 1, "bypassed": True},
        {"family": "FUZZ", "instance": 2, "bypassed": False}], "mods": {}}
    _, warns = server.validate_action(server.Action(
        kind="set_param", block="drive", instance=2, param="FUZZ_DRIVE", value=5))
    assert not any("BYPASSED" in w for w in warns)


def test_it_survives_never_having_read_the_rig(client):
    server._last_snapshot["state"] = None
    _, warns = server.validate_action(server.Action(
        kind="set_param", block="drive", instance=1, param="FUZZ_DRIVE", value=5))
    assert not any("BYPASSED" in w for w in warns)


# --- the two views must agree ---------------------------------------------

def test_the_chain_and_the_panels_read_one_source(client):
    """The signal chain draws from s.blocks and so, now, do the panels."""
    blocks = client.get("/api/state").json()["blocks"]
    assert blocks and all("bypassed" in b for b in blocks)
    assert all("family" in b and "instance" in b for b in blocks)
