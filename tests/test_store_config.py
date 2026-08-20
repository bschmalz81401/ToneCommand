"""Store whitelist configuration behavior."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from fm9.device import get_store_slots
from fm9.sim import SimFM9


def test_parse_range(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "133-148")
    assert get_store_slots() == set(range(133, 149))


def test_parse_mixed(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "5, 140, 150-152")
    assert get_store_slots() == {5, 140, 150, 151, 152}


def test_garbage_and_out_of_range_ignored(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "abc, 600, 140")
    assert get_store_slots() == {140}


def test_unconfigured_disables_store(monkeypatch, tmp_path):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "")
    import fm9.device as device
    monkeypatch.setattr(device, "Path", lambda *a: tmp_path / "nope")
    fm9 = SimFM9()
    with pytest.raises(PermissionError, match="disabled"):
        fm9.store_preset(133)


def test_configured_slot_allowed(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "133-148")
    fm9 = SimFM9()
    fm9.status_dump()
    assert fm9.store_preset(140)


def test_outside_configured_refused(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "133-148")
    fm9 = SimFM9()
    with pytest.raises(PermissionError, match="refused"):
        fm9.store_preset(509)
