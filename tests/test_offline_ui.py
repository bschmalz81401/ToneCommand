"""With the rig off, half the page is scenery. Take it away.

Moncy's framing: idiot proof it. A slider that cannot move, a scan button that
cannot scan and a scene button that switches nothing are all worse than
absent, because each one invites a click that goes nowhere and teaches you the
tool is broken rather than that the cable is out.

Greying them was the first attempt. Hiding them is simpler and strictly
better: a hidden element is out of the layout AND out of the tab order, so the
bookkeeping that remembered which controls were ALREADY disabled, and the bug
where reconnecting switched those back on, stop existing rather than being
handled.

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


def test_a_panel_that_needs_the_rig_is_removed_not_faded():
    """Out of the layout and out of the tab order in one move."""
    assert "body.rig-off .needs-rig { display: none; }" in STYLE


def test_nothing_is_disabled_by_hand_any_more():
    """That bookkeeping existed only to stop a greyed control being reachable.
    Hiding removes the need, and with it the bug where reconnecting re-enabled
    a control that had been disabled for its own reason."""
    fn = SCRIPT.split("function setRigOff")[1].split("\n}")[0]
    # code, not prose: the comment explaining why this went away naturally
    # mentions the word, and asserting on text rather than behaviour is how a
    # test breaks for no reason
    code = "\n".join(l for l in fn.splitlines() if not l.strip().startswith("//"))
    assert ".disabled" not in code
    assert "wasDisabled" not in SCRIPT
    # the whole switch is now one class toggle
    assert "classList.toggle('rig-off', off)" in code


def test_transmit_is_hidden_even_though_its_panel_works():
    """A plan can be BUILT with the rig off, which is the whole point of the
    designs work. It just cannot be sent."""
    assert "body.rig-off #apply { display: none; }" in STYLE
    assert "COMMAND" in _panels(False)


def test_the_preset_pill_goes_too():
    """There is nothing to switch to."""
    assert "body.rig-off #preset { display: none; }" in STYLE


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
    banner = UI.split('class="offbanner"')[1].split("</div>")[0].lower()
    assert "not connected" in banner
    # it NAMES what went, so nothing has silently vanished
    assert "hidden" in banner and "scenes" in banner
    # and says what still works, since "not connected" alone reads as
    # "nothing works", which is wrong and discouraging
    assert "design" in banner and "lost" in banner


# --- the one piece of state with no way to correct it --------------------

def test_the_link_pill_is_a_button():
    """A status light you cannot press is useless when the status is wrong,
    and it was wrong in a way nothing could fix: the poll retries, but through
    a MIDI bus view the backend caches for the life of the process, so an FM9
    switched on after the server started stayed invisible however long you
    waited. Restarting the server was the only cure."""
    assert re.search(r'<button class="pill off" id="link"', UI)
    assert "$('link').onclick = reconnect" in SCRIPT


def test_reconnecting_rescans_the_bus_rather_than_just_retrying():
    """Retrying through the same stale client would find the same nothing."""
    import inspect
    import server
    assert hasattr(server, "rescan_midi")
    src = inspect.getsource(server.rescan_midi)
    assert "set_backend" in src and "load=True" in src
    endpoint = inspect.getsource(server.api_reconnect)
    assert "drop_fm9()" in endpoint and "rescan_midi()" in endpoint


def test_a_failed_reconnect_says_why():
    """"Still nothing" is an answer; a silent no-op is not."""
    import inspect
    import server
    src = inspect.getsource(server.api_reconnect)
    assert '"why"' in src
    fn = SCRIPT.split("async function reconnect()")[1].split("\n}\n")[0]
    assert "still no FM9" in fn and "FM9-Edit is not holding the port" in fn


def test_the_poll_does_not_stamp_over_a_reconnect_in_progress():
    """The five second poll rewrites the pill's class, which would wipe the
    busy state mid-look and make the button appear to do nothing."""
    assert SCRIPT.count("classList.contains('busy')") >= 2
