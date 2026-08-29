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
