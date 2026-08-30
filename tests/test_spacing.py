"""Room to breathe.

Moncy: "it looks so cramped and that could make it a little scary for those
less tech savvy". Density reads as "this is for experts" whether or not it is
true, and this tool is aimed at guitarists rather than engineers. A longer page
costs a scroll. A cramped one costs the reader.

The numbers were 18px of padding inside a panel, 20px between panels, and
line-height: normal, which on a monospace face at this size sets lines almost
touching.
"""
import re
from pathlib import Path

UI = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
STYLE = UI.split("<style>")[1].split("</style>")[0]


def _var(name):
    return int(re.search(rf"--{name}:\s*(\d+)px", STYLE).group(1))


def test_spacing_comes_from_tokens_not_scattered_numbers():
    """So opening it up further is one edit rather than thirty."""
    for name in ("pad", "gap", "row"):
        assert re.search(rf"--{name}:\s*\d+px", STYLE), name
    console = STYLE.split(".console {")[1].split("}")[0]
    assert "var(--pad)" in console and "var(--gap)" in console


def test_panels_have_real_padding():
    assert _var("pad") >= 24, "18px was the cramped value"


def test_panels_are_not_touching():
    assert _var("gap") >= 24


def test_text_is_not_set_solid():
    """line-height: normal on a monospace face at this size puts lines almost
    touching, and every paragraph on the page was harder to read for it."""
    body = STYLE.split("  body {")[1].split("}")[0]
    m = re.search(r"line-height: ([\d.]+)", body)
    assert m and float(m.group(1)) >= 1.5


def test_the_hint_is_separated_from_what_it_explains():
    # anchored on the rule itself: a plain split matched a more specific
    # ".hint" further up and reported the base rule as unset
    hint = re.search(r"^\s*\.hint \{([^}]*)\}", STYLE, re.M).group(1)
    assert int(re.search(r"margin-top: (\d+)px", hint).group(1)) >= 14
    assert float(re.search(r"line-height: ([\d.]+)", hint).group(1)) >= 1.5


def test_the_parameter_label_column_fits_the_longest_label():
    """"Master Volume" is 13 characters and was wrapping onto two lines, which
    reads as a fault rather than as a layout."""
    knob = STYLE.split(".knob { display: grid;")[1].split("}")[0]
    px = int(re.search(r"grid-template-columns: (\d+)px", knob).group(1))
    assert px >= 140
