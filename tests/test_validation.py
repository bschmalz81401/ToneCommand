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
