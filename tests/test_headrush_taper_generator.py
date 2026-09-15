"""The extraction, as a function on synthetic bundles.

#33, #126. `tests/test_headrush_tapers.py` checks the committed artifact and
the Python curves against it. Nothing checked the REGEXES that pull the vendor's
code out of a megabyte of minified JavaScript, and those are the part with the
most room to be quietly wrong: a pattern that matched an adjacent construct
would produce a table that looked right.

Independent review raised this. No unit, no network, no node, no bundle.
"""
import pytest

from tools import build_headrush_tapers as gen


MINIMAL = (
    'junk();function Ze(e,t,n){return null==n?Math.min(e,t):Math.min(Math.max(e,t),n)}'
    'more();function yf(e){return Math.pow(10,.05*e)}'
    'const vf=.044282303954338874,wf=yf(-14),Ef=-14/.75,Sf=wf/.25;'
    'var kf;!function(e){e[e.Linear=0]="Linear",e[e.Squared=5]="Squared"}(kf||(kf={}));'
    'const xf={[kf.Linear]:(e,{minimum:t,maximum:n})=>(e-t)/(n-t)},'
    'Af={[kf.Linear]:(e,{minimum:t,maximum:n})=>(n-t)*e+t};'
    'function Lf(e,t){return 1}function If(e,t){return 2}function zz(e){return 3}'
)


def test_each_fragment_is_pulled_out_of_a_minified_bundle():
    got = gen.extract(MINIMAL)
    assert set(got) == {"enum", "normalise", "denormalise", "helpers",
                        "clamp", "dispatch"}
    assert "[kf.Linear]" in got["normalise"] and "[kf.Linear]" in got["denormalise"]
    assert got["normalise"] != got["denormalise"], "two directions, not one twice"
    assert "Math.min" in got["clamp"]
    assert got["dispatch"].startswith("function Lf(e,t)")
    assert "function If(e,t)" in got["dispatch"]


def test_the_two_tables_are_not_confused_with_each_other():
    """`Af` is anchored on the `},Af={` that follows `xf`, and `xf` on the text
    before it. Swapping the two would invert every conversion silently."""
    got = gen.extract(MINIMAL)
    assert "(e-t)/(n-t)" in got["normalise"], "normalising divides by the span"
    assert "(n-t)*e+t" in got["denormalise"], "denormalising multiplies by it"


def test_the_names_come_out_with_their_ordinals():
    names = gen.names_from(gen.extract(MINIMAL)["enum"])
    assert names == {0: "Linear", 5: "Squared"}


@pytest.mark.parametrize("piece, removed", [
    ("clamp", "function Ze(e,t,n){return null==n?Math.min(e,t):Math.min(Math.max(e,t),n)}"),
    ("helpers", "function yf(e){return Math.pow(10,.05*e)}"),
    ("dispatch", "function If(e,t){return 2}"),
])
def test_a_bundle_missing_a_fragment_refuses(piece, removed):
    """Refuse, not match something adjacent. A rebuilt bundle should stop this
    generator rather than have it extract the wrong thing and keep going."""
    with pytest.raises(SystemExit, match=piece):
        gen.extract(MINIMAL.replace(removed, ""))


def test_a_bundle_with_the_wrong_number_of_tapers_refuses():
    """The committed table has eleven. A bundle parsing to two means the
    patterns matched something, and something is not the same as the right
    thing."""
    with pytest.raises(SystemExit, match="expected 11 tapers"):
        gen.build(MINIMAL, "synthetic")


def test_the_hardware_readings_are_a_write_gate_not_a_comment():
    """The generator refuses if the extracted formulas disagree with what a
    real unit showed. Exercised by feeding `verify` a vector set that is wrong
    on purpose, so the gate is known to fire rather than assumed to."""
    wrong = [{"algo": a, "minimum": lo, "maximum": hi, "wire": w,
              "display": 999.0, "roundTrip": None}
             for _, a, lo, hi, w, _, _ in gen.HARDWARE]
    problems = gen.verify(wrong)
    assert len(problems) == len(gen.HARDWARE)
    assert "the unit's screen showed" in problems[0]

    right = [{"algo": 0, "minimum": 0.0, "maximum": 100.0, "wire": 0.75,
              "display": 75.0, "roundTrip": None}]
    assert "Amp.Bass" not in " ".join(gen.verify(right))


def test_a_missing_vector_is_a_problem_not_a_pass():
    """An empty vector set must not read as "nothing disagreed"."""
    problems = gen.verify([])
    assert len(problems) == len(gen.HARDWARE)
    assert all("no vector" in p for p in problems)
