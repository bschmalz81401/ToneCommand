"""With the rig off, half the page is scenery. Say so.

Moncy's framing: idiot proof it. A slider that cannot move, a scan button that
cannot scan and a scene button that switches nothing are all worse than absent,
because each one invites a click that goes nowhere and teaches you the tool is
broken rather than that the cable is out.

The other half of the same rule matters just as much: the panels that DO work
offline stay at full brightness. Dimming those would be the same lie in the
other direction, since designing tones, browsing recipes and reviewing what is
queued are all perfectly good with the unit unplugged.
"""
import re
from pathlib import Path

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
SCRIPT = UI.split("<script>")[1]
STYLE = UI.split("<style>")[1].split("</style>")[0]


def _panels(with_class: bool):
    out = []
    for m in re.finditer(r'<div class="console([^"]*)" data-label="([^"]+)"', UI):
        has = "needs-rig" in m.group(1)
        if has == with_class:
            out.append(m.group(2))
    return out


def test_the_panels_that_need_hardware_are_marked():
    marked = set(_panels(True))
    assert marked == {"SCENES", "SIGNAL CHAIN", "TONE", "UNDO / COMPARE",
                      "PRESET HEALTH", "SAVE TO PRESET"}, marked


def test_the_panels_that_work_offline_are_not_dimmed():
    """Planning, recipes and designs are the reason offline mode exists."""
    live = set(_panels(False))
    for panel in ("COMMAND", "TONE RECIPES", "DESIGNED PRESETS", "LOG"):
        assert panel in live, panel


def test_a_dimmed_panel_also_stops_taking_input():
    """A control you can still reach by keyboard while it looks dead is a trap
    rather than a hint, so the class dims it AND every control inside is
    really disabled."""
    assert "body.rig-off .needs-rig" in STYLE
    assert "pointer-events: none" in STYLE.split("body.rig-off .needs-rig")[1][:200]
    fn = SCRIPT.split("function setRigOff")[1].split("\n}")[0]
    assert "el.disabled = true" in fn


def test_reconnecting_does_not_re_enable_what_was_already_disabled():
    """UNDO with nothing to undo, or SEND with no design selected, were
    disabled for their own reasons. Coming back online must not switch those
    on, or the state is a lie in the opposite direction."""
    fn = SCRIPT.split("function setRigOff")[1].split("\n}")[0]
    assert "wasDisabled" in fn
    assert "else if (!el.dataset.wasDisabled)" in fn


def test_transmit_is_blocked_even_though_its_panel_works():
    """A plan can be BUILT with the rig off, which is the whole point of the
    designs work. It just cannot be sent."""
    assert "body.rig-off #apply" in STYLE
    assert "COMMAND" in _panels(False)


def test_the_preset_pill_is_blocked_too():
    """There is nothing to switch to."""
    assert "body.rig-off #preset" in STYLE


def test_one_switch_decides_it():
    """So a panel cannot end up dimmed but clickable, or bright but dead."""
    assert SCRIPT.count("function setRigOff") == 1
    assert SCRIPT.count("setRigOff(true)") >= 1
    assert SCRIPT.count("setRigOff(false)") >= 1
    # and it is driven by the poll, which is the only thing that knows
    refresh = SCRIPT.split("async function refresh()")[1].split("\n}\n")[0]
    assert "setRigOff(" in refresh


def test_the_banner_says_what_still_works():
    """"Not connected" alone reads as "nothing works", which is wrong and
    discouraging: everything you build offline is kept and goes out later."""
    banner = UI.split('class="offbanner"')[1].split("</div>")[0]
    assert "not connected" in banner.lower()
    assert "design" in banner.lower() and "kept" in banner.lower()
