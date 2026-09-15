"""The normalisation tapers, checked against the vendor's own output.

#33, #126. The point of this file is the first test: every curve in
`devices/headrush/tapers.py` is compared against reference vectors produced by
RUNNING the vendor's editor code, not against anyone's reading of it. A typo in
`Db` or `AllenHeathFaderVolume` returns a plausible number and would otherwise
ship.

No unit, no network, no node. The vectors are committed.
"""
import json
import math

import pytest

from devices.headrush import tapers as T


@pytest.fixture(scope="module")
def table():
    return T.load()


@pytest.fixture(scope="module")
def blob():
    return json.loads(T.TAPERS.read_text())


# --- the one that earns the module ---------------------------------------

def test_every_curve_matches_the_vendors_own_output(table, blob):
    """All 726 vectors, each computed by executing the vendor's function.

    Relative tolerance, because these span 1e-10 to 16000 and an absolute
    epsilon would be meaningless at both ends.
    """
    checked = 0
    for v in blob["vectors"]:
        algo, lo, hi, wire = v["algo"], v["minimum"], v["maximum"], v["wire"]
        expected = v["display"]
        if expected is None or not math.isfinite(expected):
            continue
        got = table.to_display(wire, minimum=lo, maximum=hi, algo=algo)
        assert got == pytest.approx(expected, rel=1e-9, abs=1e-12), (
            f"algo {algo} ({table.name(algo)}) at wire {wire} on {lo}..{hi}")
        checked += 1
    assert checked > 600, f"only {checked} vectors were comparable"


def test_the_vector_set_covers_every_taper_and_several_shapes(blob):
    """A test that compared ten vectors would pass with nine curves wrong."""
    algos = {v["algo"] for v in blob["vectors"]}
    assert algos == set(range(11))
    assert len(blob["vectors"]) == 726
    ranges = {(v["minimum"], v["maximum"]) for v in blob["vectors"]}
    assert len(ranges) >= 6, "percentage, dB, frequency and time shapes"
    assert {0.0, 1.0} <= {v["wire"] for v in blob["vectors"]}, "endpoints"


def test_round_tripping_returns_the_wire_value(table, blob):
    """to_wire is the inverse the vendor ships, not one derived here, so it
    has to actually invert. Skips the curves that genuinely are not injective
    over the sampled range."""
    for v in blob["vectors"]:
        algo, lo, hi, wire = v["algo"], v["minimum"], v["maximum"], v["wire"]
        if v["display"] is None or v["roundTrip"] is None:
            continue
        if not math.isfinite(v["display"]):
            continue
        back = table.to_wire(v["display"], minimum=lo, maximum=hi, algo=algo)
        assert back == pytest.approx(v["roundTrip"], rel=1e-6, abs=1e-9), (
            f"algo {algo} ({table.name(algo)}) at wire {wire}")


# --- what the device measured, independently ------------------------------

def test_the_readings_taken_off_a_units_screen_are_reproduced(table, blob):
    """The end-to-end check: formulas out of the bundle, numbers off the
    hardware. Neither was fitted to the other."""
    for case in blob["hardware_check"]:
        shown = table.to_display(case["wire"], minimum=case["minimum"],
                                 maximum=case["maximum"], algo=case["algo"])
        formatted = (case["format"] % shown).replace("%%", "%")
        assert formatted == case["screen"], case["property"]


def test_squared_is_the_id_the_quadratic_reading_carried(table):
    """Amp.TremSpeed measured quadratic on hardware and publishes
    normalizeAlgo 5, which the vendor's enum names `Squared`. Two independent
    routes to the same answer, which is why the name is pinned."""
    assert table.name(5) == "Squared"
    assert table.to_display(0.5, minimum=0.25, maximum=20.0, algo=5) == \
        pytest.approx(5.1875)


# --- the dispatch, which is where a silent mis-scale would come from ------

def test_an_absent_algo_is_linear_because_the_vendor_says_so(table):
    """`(t.algo ? table[t.algo] : void 0) ?? table[0]`. This was a hypothesis
    the registry declined to act on, and is now the vendor's implementation."""
    assert table.resolve(None) == T.LINEAR
    assert table.resolve(0) == T.LINEAR
    assert table.to_display(0.75, minimum=0.0, maximum=100.0) == 75.0


def test_an_id_the_vendor_names_but_does_not_implement_is_linear(table, blob):
    """DelayRatio is in the enum and in neither table, so the fallback catches
    it. Recorded as its own fact rather than filed under Linear, because an id
    with no implementation and an id implemented as linear differ."""
    assert blob["unimplemented"] == [3]
    assert blob["tapers"]["3"]["name"] == "DelayRatio"
    assert blob["tapers"]["3"]["implemented"] is False
    assert table.resolve(3) == T.LINEAR


def test_an_id_outside_the_enum_raises_rather_than_defaulting(table):
    """The dangerous case. A firmware publishing a curve this table has never
    seen must not be quietly scaled as linear, which would mis-report every
    value of that parameter while looking fine."""
    with pytest.raises(T.UnknownTaper, match="has not seen"):
        table.resolve(47)
    with pytest.raises(T.UnknownTaper):
        table.to_display(0.5, minimum=0.0, maximum=1.0, algo=47)


def test_a_table_naming_a_curve_the_module_lacks_refuses_to_load(tmp_path, blob):
    """Regenerating the artifact from a newer bundle without adding the curve
    would convert it as linear, silently. It fails to load instead."""
    broken = json.loads(json.dumps(blob))
    broken["tapers"]["11"] = {"name": "SomethingNew", "implemented": True}
    path = tmp_path / "tapers.json"
    path.write_text(json.dumps(broken))
    T.load.cache_clear()
    with pytest.raises(T.UnknownTaper, match="does not implement"):
        T.load(path)
    T.load.cache_clear()


# --- provenance is not optional ------------------------------------------

def test_the_table_says_it_is_not_a_device_read(table):
    """Same class as the topology table. The unit publishes an opaque integer
    and no formula; these come from the editor it serves."""
    assert table.api_readable is False
    assert table.provenance == "vendor editor bundle"
    assert "NOT A DEVICE READ" in table.warning


def test_the_vendors_code_is_not_redistributed(blob):
    """Names and numbers are facts and are carried. The vendor's source text
    is theirs, and committing minified third-party JavaScript into this
    repository would be redistributing it with no licence that allows that.

    A sha256 prefix of each extracted fragment is kept instead, so a
    regeneration can be shown to have read the same code without the code
    travelling with it.
    """
    text = json.dumps(blob)
    assert "=>" not in text, "no JavaScript source in the artifact"
    assert "Math." not in text, "nor fragments of it"
    digests = blob["vendor_source_sha256"]
    assert set(digests) == {"to_display", "to_wire", "helpers", "clamp"}
    assert all(len(d) == 16 and int(d, 16) >= 0 for d in digests.values())
    # the two directions are genuinely different code, not one read twice
    assert digests["to_display"] != digests["to_wire"]


def test_a_value_with_no_finite_image_is_refused_not_returned(table):
    """Volume at wire 0 is log10(0). The editor shows a special string there;
    this returns no number rather than -inf."""
    with pytest.raises(T.NotConvertible):
        table.to_display(0.0, minimum=0.0, maximum=1.0, algo=2)


def test_the_points_the_vendor_has_no_number_for_are_refused_here_too(table, blob):
    """The hole in the comparison above, closed.

    39 of the 726 vectors carry `display: null`, which is JSON's rendering of
    the -Infinity the vendor returns (Volume at wire 0 is log10(0)). The main
    comparison SKIPS those, so a Python curve that happily returned a number
    where the vendor returns none would never be caught by it.

    This is also where Python and JavaScript genuinely differ: `math.log10(0)`
    raises ValueError where `Math.log10(0)` is -Infinity. The module mirrors
    the vendor and turns the result into one typed refusal, so callers see
    NotConvertible rather than a ValueError from inside a curve.
    """
    refused = 0
    for v in blob["vectors"]:
        if v["display"] is not None:
            continue
        with pytest.raises(T.NotConvertible):
            table.to_display(v["wire"], minimum=v["minimum"],
                             maximum=v["maximum"], algo=v["algo"])
        refused += 1
    assert refused == 39, "every point the vendor cannot express is refused"


def test_a_curve_never_leaks_a_python_domain_error(table, blob):
    """Across every vector, the only exception a caller can see is the typed
    one. A ValueError escaping from math.log10 would be a leak of this
    module's implementation into its contract."""
    for v in blob["vectors"]:
        try:
            table.to_display(v["wire"], minimum=v["minimum"],
                             maximum=v["maximum"], algo=v["algo"])
        except T.NotConvertible:
            pass
