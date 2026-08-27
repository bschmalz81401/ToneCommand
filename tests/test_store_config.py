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


# --- a refusal has to describe the rule it is enforcing (#22 review) ---

def test_a_gappy_whitelist_is_not_described_as_a_range(monkeypatch):
    """Naming lowest-to-highest calls every slot in the gap allowed, which
    sends the owner off to fix the wrong thing. Reproduced by the maintainer
    with 133,150-155: refusing 140 printed "store slots are 133-155"."""
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "133,150-155")
    fm9 = SimFM9()
    with pytest.raises(PermissionError) as err:
        fm9.store_preset(140)
    msg = str(err.value)
    assert "133-155" not in msg, "the whitelist is not contiguous"
    assert "133 (FM9-Edit 134)" in msg
    assert "150-155 (FM9-Edit 151-156)" in msg


def test_the_refused_slot_is_named_in_both_numberings(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "133-148")
    fm9 = SimFM9()
    with pytest.raises(PermissionError) as err:
        fm9.store_preset(509)
    assert "509 (FM9-Edit 510)" in str(err.value)


def test_slot_set_label_collapses_runs():
    from fm9 import protocol as p
    assert p.slot_set_label([133]) == "133 (FM9-Edit 134)"
    assert p.slot_set_label(range(133, 136)) == "133-135 (FM9-Edit 134-136)"
    assert p.slot_set_label([5, 133, 134, 135, 200]) == (
        "5 (FM9-Edit 6), 133-135 (FM9-Edit 134-136), 200 (FM9-Edit 201)")
