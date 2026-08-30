"""A graphic EQ drawn the way one looks.

Ten horizontal rows of numbers is not a graphic EQ, it is a spreadsheet of
one. The whole point of the control is that the curve is a SHAPE you read at
a glance, and every musician has already learned to read it. Moncy's framing:
"we are designing this for musicians, give them graphic EQ the way they are
used to".
"""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
UI = (ROOT / "ui" / "index.html").read_text()
SCRIPT = UI.split("<script>")[1]
STYLE = UI.split("<style>")[1].split("</style>")[0]
BODY = UI.split("</style>")[1]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    return TestClient(server.app)


# --- the shape of the control ---------------------------------------------

def test_the_faders_stand_up():
    fader = re.search(r"^\s*\.geq \.vfader \{([^}]*)\}", STYLE, re.M).group(1)
    assert "writing-mode: vertical-lr" in fader
    assert "direction: rtl" in fader


def test_flat_is_a_line_you_can_see():
    """So a curve reads as a departure from it rather than as ten unrelated
    numbers."""
    assert re.search(r"^\s*\.geqzero \{", STYLE, re.M)
    assert 'class="geqzero"' in SCRIPT


def test_it_gets_its_own_full_width_panel():
    """TONE is a newspaper-column layout (columns: 340px). A curve squeezed
    into a third of the page is a curve you have to squint at, so the EQ
    leaves that grid rather than living in one of its columns."""
    assert 'data-label="GRAPHIC EQ"' in BODY and 'id="geqpanel"' in BODY
    render = SCRIPT.split("function renderParams")[1].split("\nfunction ")[0]
    assert "renderGeq(bands" in render
    assert "continue;" in render, "it must not also be pushed as a TONE column"


def test_only_the_bands_become_faders():
    """A graphic EQ also carries an output Level, which is an ordinary control
    and stays one."""
    render = SCRIPT.split("function renderParams")[1].split("\nfunction ")[0]
    assert "fam === 'GEQ'" in render
    # the bands are the symmetric ones; Level runs -80 to 20 and is not
    assert "Math.abs(meta[k].min + meta[k].max) < 0.01" in render
    fn = SCRIPT.split("function renderGeq")[1].split("\n}\n")[0]
    assert "rest.map(k => knobRow" in fn


def test_the_panel_disappears_with_the_block():
    """Most presets have no graphic EQ. An empty panel headed GRAPHIC EQ says
    the rig has one and it is flat, which is a false statement."""
    render = SCRIPT.split("function renderParams")[1].split("\nfunction ")[0]
    assert "geqKeys = [];" in render, "stale keys would keep a dead panel open"
    assert "if (!geqKeys.length) $('geqpanel').style.display = 'none';" in render


# --- one write path --------------------------------------------------------

def test_there_is_still_only_one_write_path():
    """A second way to set a parameter is a second thing to keep correct."""
    fn = SCRIPT.split("function renderGeq")[1].split("\n}\n")[0]
    assert 'data-key="${esc(k)}"' in fn
    # every handler finds its row the same way, whichever row shape it is
    assert SCRIPT.count("closest('[data-key]')") >= 3
    assert "closest('.knob')" not in SCRIPT


def test_a_curve_is_one_trip_not_ten():
    """Ten separate calls would take ten undo snapshots, leaving nine ways to
    end up half applied and no single thing to press UNDO on."""
    fn = SCRIPT.split("async function geqSet")[1].split("\n}\n")[0]
    assert "blockAction(actions" in fn
    assert "fetch(" not in fn, "batching must not become a second write path"
    ba = SCRIPT.split("async function blockAction")[1].split("\n}\n")[0]
    assert "Array.isArray(action) ? action : [action]" in ba


def test_a_batch_reports_the_first_failure_rather_than_the_first_result():
    """Reading results[0] would call a curve applied when band 7 was refused."""
    ba = SCRIPT.split("async function blockAction")[1].split("\n}\n")[0]
    assert "results.find(x => !x.ok)" in ba
    # the server sends action:null for notes about the plan, not about a write
    assert "filter(x => x.action)" in ba


def test_a_curve_is_clamped_to_what_the_bands_allow():
    """A shape drawn for a +/-12 dB EQ handed to a narrower one must land on
    the rail, not be refused band by band."""
    fn = SCRIPT.split("async function geqSet")[1].split("\n}\n")[0]
    assert "Math.max(m.min, Math.min(m.max, vals[i]))" in fn


# --- what a band label can honestly say ------------------------------------

def test_the_bands_carry_a_range_not_a_frequency():
    """The catalogue gives each band a displayLabel, but they are not
    ascending and one value appears twice: "1.6K" at band 5 and "1600" at
    band 7. GEQ_TYPE is an eighteen-value enum selecting the band layout, and
    one label per parameter cannot describe eighteen layouts. What IS true of
    every graphic EQ ever built is that the bands ascend left to right, so the
    strip names the region instead.
    """
    assert "const GEQ_ZONES = ['LOW', 'LOW MID', 'MID', 'HIGH MID', 'HIGH']" in SCRIPT
    fn = SCRIPT.split("function renderGeq")[1].split("\n}\n")[0]
    assert '<span class="bandhz">${i + 1}</span>' in fn
    assert 'class="geqzones"' in fn
    # and no catalogue label reaches the fader
    assert "m.label" not in fn


def test_the_range_strip_lines_up_with_the_faders():
    """Five equal cells over seven bands would put MID under the wrong fader.
    Each cell grows to the number of bands it actually covers."""
    fn = SCRIPT.split("function renderGeq")[1].split("\n}\n")[0]
    assert "flex:${span[z]} 1 0" in fn
    assert "GEQ_ZONES.filter(z => span[z])" in fn, "an empty zone must not draw"


def test_the_catalogue_labels_really_are_inconsistent():
    """Pins the reason above to the data rather than to a memory of it, so if
    the catalogue is ever corrected this test says so."""
    labels = [server.reg.spec("GEQ", pid, 1).label for pid in range(10)]
    nums = []
    for l in labels:
        m = re.fullmatch(r"([\d.]+)([kK]?)", l)
        nums.append(float(m.group(1)) * (1000 if m.group(2) else 1) if m else None)
    assert nums != sorted(n for n in nums if n is not None), \
        "the catalogue labels ascend now; the bands could carry frequencies"


def test_geq_type_is_an_enum_wide_enough_to_explain_it():
    """Eighteen layouts, one label per parameter."""
    assert server.reg.spec("GEQ", 15, 1).enum_count == 18


# --- starting points, and getting back to flat ------------------------------

def test_flatten_is_one_click():
    """Ten drags to get back to flat is the kind of chore that stops people
    experimenting in the first place."""
    assert 'id="geqflat"' in BODY
    assert "$('geqflat').onclick = () => geqSet(geqKeys.map(() => 0)" in SCRIPT


def test_the_curves_say_they_are_ours():
    """Nothing here came off the FM9. Presenting a curve this project drew as
    a factory setting would be inventing a spec for someone's rig."""
    fn = SCRIPT.split("function renderGeq")[1].split("\n}\n")[0]
    assert "not FM9 factory settings" in fn


def test_flat_is_among_the_curves_and_is_actually_flat():
    curves = _curves()
    assert curves[0][0] == "Flat"
    assert set(curves[0][1]) == {0}


def test_every_curve_fits_a_twelve_db_eq():
    """The bands on this rig run -12 to +12. A shape that only exists after
    clamping is not the shape anybody drew."""
    for name, shape in _curves():
        assert max(abs(v) for v in shape) <= 12, name
        assert len(shape) == 10, name


def test_a_curve_resamples_to_the_band_count():
    """The FM9's graphic EQ has a selectable band layout, so a shape drawn for
    ten positions has to still mean the same shape on seven or eleven."""
    fn = SCRIPT.split("function sampleCurve")[1].split("\n}\n")[0]
    assert "shape.length - 1" in fn and "Math.round(" in fn
    assert "if (n === shape.length) return shape.slice();" in fn


def test_choosing_a_curve_does_not_leave_it_selected():
    """It is an action, not a stored setting. Left showing "Scooped" the box
    would claim the EQ is on a preset it may since have been dragged off."""
    assert "e.target.value = '';" in SCRIPT.split("$('geqcurve').onchange")[1][:400]


def _curves():
    block = SCRIPT.split("const GEQ_CURVES = [")[1].split("\n];")[0]
    out = []
    for line in block.splitlines():
        m = re.search(r"\['([^']+)',\s*\[([^\]]*)\]", line)
        if m:
            out.append((m.group(1), [float(x) for x in m.group(2).split(",")]))
    return out


# --- and it is an ordinary verified write at the far end -------------------

def test_a_band_write_is_an_ordinary_verified_set(client):
    r = client.post("/api/apply", json={"actions": [{
        "kind": "set_param", "block": "GEQ", "instance": 1,
        "param": "GEQ_GAIN5", "value": 4.5}]}).json()
    assert r["results"][-1]["ok"], r["results"]


def test_a_whole_curve_verifies_band_by_band(client):
    """Not "sent, probably fine". Each band is read back like any other
    write, which is what makes an undo of the curve meaningful."""
    acts = [{"kind": "set_param", "block": "GEQ", "instance": 1,
             "param": f"GEQ_GAIN{i}", "value": v}
            for i, v in enumerate([3, 2.5, 0.5, -3, -5, -4.5, -1.5, 2, 3, 2], 1)]
    r = client.post("/api/apply", json={"actions": acts}).json()
    assert all(x["ok"] for x in r["results"] if x["action"]), r["results"]
    assert all(x["detail"] for x in r["results"] if x["action"])


def test_the_simulator_actually_has_one(client):
    """A renderer nothing exercises is a renderer nobody notices breaking."""
    vals = client.get("/api/state").json()["values"]
    assert [f"GEQ_GAIN{i}" for i in range(1, 11)] == \
        [k for k in vals if k.startswith("GEQ_GAIN")]
