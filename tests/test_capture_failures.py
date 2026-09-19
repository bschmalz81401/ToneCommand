"""Issue #150 (I7): every capture failure is one line that says what was
done instead, none of them raises, and a build is never left half-applied.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import server
from fm9 import capture_failures as cf
from fm9.adapter import CaptureInstall
from fm9.sim import SimFM9
from tests.test_capture_primitive import _record


def test_no_capture_builds_with_the_model_and_says_so():
    line = cf.no_capture("PVH 6160 Block Lead", asked_for="a real 5150")
    assert line == ("no suitable capture for a real 5150 on file; built with "
                    "the Fractal PVH 6160 Block Lead instead")
    assert cf.answer("no_capture", model_name="Brit 800") == \
        "no suitable capture on file; built with the Fractal Brit 800 instead"


def test_too_heavy_uses_a_lighter_variant_when_offered_else_the_model():
    assert cf.too_heavy("BE-100 standard", "Friedman BE", "BE-100 lite") == \
        ("BE-100 standard is too heavy for this unit; used the lighter variant "
         "BE-100 lite of the same capture instead")
    assert cf.too_heavy("BE-100 standard", "Friedman BE") == \
        ("BE-100 standard is too heavy for this unit and no lighter variant is "
         "offered; built with the Fractal Friedman BE instead")
    # the verdict itself is the caller's (I0 decides); nothing here judges size
    assert cf.answer("too_heavy", capture_name="x", model_name="m").endswith("instead")


def test_no_nam_support_is_the_model_in_one_line_no_error():
    line = cf.no_nam_support("Brit 800", device_label="the FM9 on firmware 12.00")
    assert line == ("the FM9 on firmware 12.00 plays no captures (no NAM support "
                    "on this firmware); built with the Fractal Brit 800 instead")


def test_gig_gate_is_the_existing_refusal_word_for_word():
    assert cf.gig_gate() == "GIG LOCK is on: refusing to touch the rig."
    assert cf.gig_gate() in (server.__file__ and open(server.__file__).read())


def test_readback_mismatch_stops_reports_and_never_retries(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_NAM_SLOTS", "0-6")
    sim = SimFM9(server.reg)
    calls = []
    real = sim.install_capture

    def flaky(record, raw, slot):
        calls.append(slot)
        res = real(record, raw, slot)
        if slot == 2:                              # the unit disagrees once
            return CaptureInstall(slot, False, "read-back mismatch")
        return res
    plan = [(_record("a"), b"\x01", 1), (_record("b"), b"\x02", 2),
            (_record("c"), b"\x03", 3)]
    out = cf.guarded_install(flaky, plan)
    assert out["done"] == [1] and out["stopped_at"] == 2
    assert calls == [1, 2]                         # 3 never attempted, 2 not retried
    assert out["line"].startswith("capture install to slot 2 stopped: the slot did not read back as")
    assert "not retried" in out["line"]
    # a whitelist refusal stops the same way, with its own line
    out = cf.guarded_install(real, [(_record("d"), b"\x04", 7)])   # not whitelisted
    assert out["stopped_at"] == 7 and "refused" in out["line"]
    # and a plan that reads back all the way says so
    out = cf.guarded_install(real, [(_record("e"), b"\x05", 4)])
    assert out == {"done": [4], "stopped_at": None,
                   "line": "1 capture(s) installed and read back"}


def test_every_path_answers_without_raising():
    facts = {
        "no_capture": {"model_name": "m"},
        "too_heavy": {"capture_name": "c", "model_name": "m"},
        "no_nam_support": {"model_name": "m"},
        "gig_gate": {},
        "readback_mismatch": {"slot": 1, "expected": "x"},
    }
    for path, f in facts.items():
        line = cf.answer(path, **f)
        assert isinstance(line, str) and line and "\n" not in line
    with pytest.raises(KeyError):
        cf.answer("something_else")
