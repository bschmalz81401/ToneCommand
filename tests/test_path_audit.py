"""The path auditor must prove life AND detect death (simulator)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fm9.registry import Registry
from fm9.sim import SimFM9
from tools.path_audit import scene_alive


def _state(dev):
    time.sleep(0.1)   # clear the sim's async-settle window, like hardware
    return {b.effect_id: b for b in dev.status_dump()}


def test_default_sim_preset_is_alive():
    reg = Registry()
    dev = SimFM9(reg)
    ok, why = scene_alive(dev.read_grid(), _state(dev), reg)
    assert ok, why


def test_bypassed_input_is_dead():
    reg = Registry()
    dev = SimFM9(reg)
    dev.set_bypass(reg.effect_id("INPUT"), True)
    ok, why = scene_alive(dev.read_grid(), _state(dev), reg)
    assert not ok
    assert "INPUT" in why


def test_bypassed_output_is_dead():
    reg = Registry()
    dev = SimFM9(reg)
    dev.set_bypass(reg.effect_id("OUTPUT"), True)
    ok, why = scene_alive(dev.read_grid(), _state(dev), reg)
    assert not ok
