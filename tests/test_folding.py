"""Fold away the panels you are not using.

The page grew from four panels to nine, and the tone panel from five groups to
ten, so not everyone needs the routing grid and the recipe browser open at
once. Moncy asked for it once the page had enough on it to be worth folding.

Built in script rather than written into nine places in the markup, so a new
panel gets this for free and they cannot drift apart.
"""
import re
from pathlib import Path

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
SCRIPT = UI.split("<script>")[1]
STYLE = UI.split("<style>")[1].split("</style>")[0]


def test_the_headers_are_generated_not_written_out():
    """Nine hand-written headers is nine chances to forget one."""
    assert "function buildPanelHeaders" in SCRIPT
    assert 'querySelectorAll(\'.console[data-label]\')' in SCRIPT
    assert UI.count('class="phead"') == 0, "a header was hardcoded"


def test_the_label_became_a_real_button():
    """A ::before pseudo-element cannot be clicked or reached by keyboard."""
    assert ".console::before { content: none; }" in STYLE
    fn = SCRIPT.split("function buildPanelHeaders")[1].split("\n}")[0]
    assert "createElement('button')" in fn
    assert "head.type = 'button'" in fn


def test_it_says_whether_it_is_open():
    """Folding is invisible to a screen reader without it."""
    assert "aria-expanded" in SCRIPT
    fold = SCRIPT.split("function setFold")[1].split("\n}")[0]
    assert "aria-expanded" in fold


def test_the_plan_box_is_left_alone():
    """It is shown and hidden by the code that fills it, so folding would be
    two mechanisms fighting over the same panel."""
    fn = SCRIPT.split("function buildPanelHeaders")[1].split("\n}")[0]
    assert "c.id === 'planbox'" in fn


def test_a_folded_panel_is_a_label_not_a_panel():
    """Keeping a panel's worth of padding under a folded label would defeat
    most of the point."""
    assert ".console.folded .pbody { display: none; }" in STYLE
    folded = re.search(r"^\s*\.console\.folded \{([^}]*)\}", STYLE, re.M).group(1)
    assert "padding-top" in folded and "padding-bottom" in folded


def test_the_caret_turns_rather_than_swapping():
    """Down for open, right for folded, the one convention everybody knows."""
    assert ".console.folded .pcaret { transform: rotate(-90deg); }" in STYLE
    caret = re.search(r"^\s*\.pcaret \{([^}]*)\}", STYLE, re.M).group(1)
    # drawn, like every other arrow on the page
    assert "border-left: 6px solid transparent" in caret


def test_the_choice_is_remembered():
    assert "localStorage.setItem(FOLD_KEY" in SCRIPT
    assert "localStorage.getItem(FOLD_KEY" in SCRIPT


def test_a_browser_that_refuses_storage_still_works():
    """Private windows throw rather than returning null, and a page that will
    not render because it could not read a preference is a bad trade."""
    read = SCRIPT.split("function foldedSet")[1].split("\n}")[0]
    assert "catch" in read and "new Set()" in read
    write = SCRIPT.split("function rememberFolds")[1].split("\n}")[0]
    assert "catch" in write
