"""Validation-before-send regression suite (no hardware, no simulator)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import server
from server import Action, validate_action

CASES = [
    ("in-range", dict(kind="set_param", block="amp", param="DISTORT_DRIVE", value=6.5), 0, 0),
    ("over-range", dict(kind="set_param", block="amp", param="DISTORT_DRIVE", value=15), 1, 0),
    ("under-range", dict(kind="set_param", block="amp", param="DISTORT_DRIVE", value=-2), 1, 0),
    ("unknown-param", dict(kind="set_param", block="amp", param="DISTORT_MOJO", value=5), 1, 0),
    ("unknown-block", dict(kind="set_param", block="kazoo", param="X", value=5), 1, 0),
    ("selector-as-param", dict(kind="set_param", block="amp", param="DISTORT_TYPE", value=3), 1, 0),
    ("non-numeric", dict(kind="set_param", block="amp", param="DISTORT_DRIVE", value=None), 1, 0),
    ("uncalibrated-warns", dict(kind="set_param", block="geq", param="GEQ_MIX", value=50), 0, 1),
    ("scene-ok", dict(kind="set_scene", value=3), 0, 0),
    ("scene-9", dict(kind="set_scene", value=9), 1, 0),
    ("tempo-500", dict(kind="set_tempo", value=500), 1, 0),
    ("tempo-120", dict(kind="set_tempo", value=120), 0, 0),
    ("channel-5", dict(kind="set_channel", block="amp", value=5), 1, 0),
    ("good-model", dict(kind="set_type", block="amp", type_name="PVH 6160 Block Lead"), 0, 0),
    ("real-amp-name", dict(kind="set_type", block="amp", type_name="MESA/Boogie Mark IIC+"), 0, 0),
    ("garbage-model", dict(kind="set_type", block="amp", type_name="Fnord Blaster 9000"), 1, 0),
    ("bypass-no-bool", dict(kind="set_bypass", block="delay"), 1, 0),
    ("bypass-ok", dict(kind="set_bypass", block="delay", bypassed=True), 0, 0),
]


@pytest.mark.parametrize("name,action,want_errs,want_warns",
                         CASES, ids=[c[0] for c in CASES])
def test_validation(name, action, want_errs, want_warns):
    errs, warns = validate_action(Action(**action))
    assert (len(errs) > 0) == (want_errs > 0), errs
    assert (len(warns) > 0) == (want_warns > 0), warns


def test_add_block_and_bind_pedal_carry_honesty_warnings():
    """Factory defaults are not a sound, and pedal curves are undecoded
    (issues #11/#12, hardware session 2026-08-20): both must warn."""
    from server import Action, validate_action
    errs, warns = validate_action(Action(kind="add_block", block="phaser"))
    assert not errs and any("factory-default" in w for w in warns)
    errs, warns = validate_action(
        Action(kind="bind_pedal", block="delay", param="DELAY_MIX"))
    assert any("NOT verified" in w for w in warns)


def test_cab_description_resolves_real_cabinet():
    """Uses the accessor Brian shipped in #14: (ordinal, bank)."""
    from fm9.registry import Registry
    reg = Registry()
    d = reg.cab_description(4, 0)
    assert "=" in d and "Danelectro" in d          # bank0/4 per merged sidecar
    assert reg.cab_description(999, 9) == "999"    # graceful unknown


def test_effect_type_models_load_and_reach_planner():
    from fm9.registry import Registry
    from server import param_reference
    reg = Registry()
    assert reg.effect_type_models["chorus_types"]["Small Copy"].startswith("EHX")
    ref = param_reference()
    assert "Deluxe Mind Guy = Deluxe Memory Man" in ref
    assert "Dytronics Songbird" in ref
    assert "Aurora Delay = Keeley HALO" in ref
    assert reg.effect_type_models["known_ordinals"]["multitap"]["Aurora Delay"] == 1
