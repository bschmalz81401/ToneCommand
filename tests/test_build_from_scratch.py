"""Building a chain into an empty slot, and refusing when there is none.

An empty FM9 slot has no grid cells and no Input/Output blocks, so a
from-scratch build places everything and draws every cable. The rule this
suite protects: it lands on a slot the device calls <EMPTY> or it does not
run at all.
"""
import pytest

from fm9.device import NoEmptySlot
from fm9.registry import Registry
from fm9.sim import SimFM9
from tools.build_from_scratch import CHAIN, ROW, main


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.setenv("TONECOMMAND_SIM", "1")


def dev():
    return SimFM9(Registry())


# --- slot selection ---

def test_first_empty_slot_returns_the_lowest_free_one():
    with dev() as d:
        assert d.first_empty_slot().number == 386


def test_first_empty_slot_honours_a_range():
    with dev() as d:
        assert d.first_empty_slot(400, 511).number == 449


def test_no_free_slot_refuses_with_a_message_about_empty_presets():
    """The build must never fall back to overwriting someone's preset."""
    with dev() as d:
        with pytest.raises(NoEmptySlot, match="no empty presets to build on"):
            d.first_empty_slot(0, 10)


def test_the_refusal_says_how_to_find_a_slot():
    with dev() as d:
        with pytest.raises(NoEmptySlot) as err:
            d.first_empty_slot(0, 10)
    assert "find_empty_slots" in str(err.value)


# --- the tool ---

def test_it_picks_an_empty_slot_by_itself(sim, capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "target: slot 386" in out and "'<EMPTY>'" in out
    assert "chain is continuous" in out


def test_it_refuses_when_the_range_holds_no_empty_slot(sim, capsys):
    assert main(["--range", "0", "10"]) == 1
    out = capsys.readouterr().out
    assert "refusing to build" in out
    assert "no empty presets to build on" in out


def test_it_refuses_an_occupied_slot_given_explicitly(sim, capsys):
    assert main(["--slot", "0"]) == 1
    out = capsys.readouterr().out
    assert "refusing to build" in out
    assert "requires a slot the device reports as <EMPTY>" in out


def test_an_explicit_empty_slot_is_honoured(sim, capsys):
    assert main(["--slot", "449"]) == 0
    assert "target: slot 449" in capsys.readouterr().out


def test_no_force_flag_exists(sim):
    """Overwriting an owned preset should not be one flag away."""
    with pytest.raises(SystemExit):
        main(["--force"])


# --- what the build produces ---

def test_every_block_lands_and_the_chain_is_continuous():
    with dev() as d:
        d.select_preset(386)
        for col, (eid, _) in enumerate(CHAIN, start=1):
            d.place_block(ROW, col, eid)
        for col in range(1, len(CHAIN)):
            d.connect_cells(ROW, col, ROW)
        cells = {c.col + 1: c for c in d.read_grid() or []}
        assert sorted(c.effect_id for c in cells.values()) == \
            sorted(eid for eid, _ in CHAIN)
        assert cells[1].cable_in_mask == 0, "the input feeds nothing upstream"
        for col in range(2, len(CHAIN) + 1):
            assert cells[col].cable_in_mask != 0, f"col {col} has no input cable"


def test_the_build_stores_nothing(sim, capsys):
    """The slot's flash name must still read <EMPTY> afterwards."""
    main([])
    with dev() as d:
        assert d.slot_name(386).name == "<EMPTY>"
        assert d.is_slot_empty(386) is True
    assert "nothing stored" in capsys.readouterr().out
