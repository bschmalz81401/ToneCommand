"""The blast-radius warning goes out when the plan that raised it goes away.

FM9 parameters live on the channel, not the scene, so changing one moves every
scene sharing that channel. The UI lights those scenes amber rather than
burying the fact in small print under the plan card.

Lighting it was the easy half. The bug this pins is the other half: the
warning belongs to a pending plan, so when the plan is discarded, refused,
applied or replaced by a clarification, the amber has to go out with it.
Clearing the set alone was not enough, because nothing repaints until the next
five-second poll, and in the meantime the UI warned about scenes that nothing
was going to touch.
"""
from pathlib import Path

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()


def test_the_warning_is_only_ever_dropped_through_the_helper():
    """One way out, so a new exit path cannot forget to repaint.

    Two mentions are allowed and no more: the declaration, and the assignment
    inside clearAffected() itself. Anything else is a path that empties the set
    while leaving the buttons lit.
    """
    assert UI.count("affectedScenes = new Set()") == 2, (
        "a code path clears the warning without un-painting it; "
        "call clearAffected() instead"
    )


def test_the_helper_removes_the_class_as_well_as_the_state():
    body = UI.split("function clearAffected()")[1].split("\n}")[0]
    assert "affectedScenes = new Set()" in body
    assert "classList.remove('willchange')" in body


def test_every_way_a_plan_can_end_puts_the_warning_out():
    for path in ("plan discarded",          # discard button
                 "needs clarification",      # planner asked a question instead
                 ):
        seg = UI.split(path)[1][:400]
        assert "clearAffected()" in UI.split(path)[0][-400:] or "clearAffected()" in seg, path


# --- AI settings sit behind a header button, not on the front page ---
# Choosing a planner backend is a once-a-month errand. It held a full console
# on the main screen, above the log, competing with the controls you touch
# every session. Moving markup is where element references quietly break, so
# these pin the move rather than trusting a read of the diff.

import re

SCRIPT = UI.split("<script>")[1]


def test_the_settings_panel_is_behind_the_button_not_on_the_page():
    modal = UI.split('<div class="modal" id="aimodal"')[1]
    for control in ("aibackend", "aikey", "aisave", "aiclearkey"):
        assert f'id="{control}"' in modal, f"{control} escaped the modal"
    assert UI.count('data-label="AI SETTINGS"') == 1
    assert 'id="aiopen"' in UI.split("<script>")[0].split('id="aimodal"')[0], \
        "no way to reach the panel from the header"


def test_it_opens_closed():
    assert re.search(r'<div class="modal" id="aimodal" hidden>', UI), \
        "the panel must start hidden or it is not out of the way at all"


def test_every_element_the_script_reaches_for_still_exists():
    """The failure mode of moving markup: a live reference to a dead id.

    Ids the script creates itself are exempt, which is why the check reads
    assignments as well as markup.
    """
    declared = set(re.findall(r'\bid="([^"]+)"', UI))
    created = set(re.findall(r"\.id = '([^']+)'", SCRIPT))
    used = set(re.findall(r"\$\('([^']+)'\)", SCRIPT))
    assert not (used - declared - created)


def test_no_id_is_declared_twice():
    """getElementById would silently pick the first, so a stray duplicate left
    behind by a move would half-work, which is worse than breaking."""
    ids = re.findall(r'\bid="([^"]+)"', UI)
    assert len(ids) == len(set(ids)), \
        [i for i in set(ids) if ids.count(i) > 1]


def test_the_gear_carries_no_label():
    """It briefly showed the backend name, which made a settings gear look
    like it was called AUTO. A label on a control names the control. Which
    model answered belongs on the plan it produced, where it already is."""
    gear = re.search(r'<button class="gear".*?</button>', UI, re.S).group(0)
    assert "<svg" in gear and "</svg>" in gear
    assert not re.search(r">\s*[A-Za-z]", gear.split("<svg")[0].split(">", 1)[1]), \
        "text next to the icon names the control, not the backend"
    assert "ailabel" not in UI


def test_the_gear_is_drawn_not_typed():
    """U+2699 is drawn small inside its own em box, so raising font-size moved
    it barely at all and it stayed a speck beside the LINK pill. A path is
    sized by the numbers we give it."""
    assert "&#9881;" not in UI
    gear = re.search(r'<button class="gear".*?</button>', UI, re.S).group(0)
    size = re.search(r'width="(\d+)"', gear)
    assert size and int(size.group(1)) >= 18, "still too small to hit comfortably"


def test_the_gear_is_last_in_the_header_and_quiet():
    """Out of the way means after the status readout, not interrupting it,
    and without the border that would make it read as a third status pill."""
    status = UI.split('<div class="status">')[1].split("</header>")[0]
    assert status.index('id="aiopen"') > status.index('id="link"')
    style = UI.split("  .gear {")[1].split("}")[0]
    assert "border: none" in style and "background: none" in style


def test_which_backend_is_driving_is_still_reachable_before_a_plan_runs():
    """Quiet is not the same as silent: the tooltip answers it on hover, and
    it reads from the saved settings rather than the dropdown, which can be
    sitting on a selection the user never saved."""
    assert "$('aiopen').title" in SCRIPT
    load = SCRIPT.split("async function loadAiSettings()")[1].split("\n}")[0]
    assert "aiGear(d.settings.backend" in load


def test_a_backend_that_cannot_run_is_flagged_on_the_button():
    """Hiding the panel must not hide a broken planner: ENGAGE would fail with
    the explanation stuck behind a button nothing told you to press."""
    assert "classList.toggle('needs'" in SCRIPT
    assert ".gear.needs" in UI


def test_closing_drops_a_typed_key():
    body = SCRIPT.split("function aiModal(")[1].split("\n}")[0]
    assert "$('aikey').value = ''" in body, \
        "a typed key must not sit in the DOM after the panel closes"
