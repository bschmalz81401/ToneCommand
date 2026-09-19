"""Issue #6, the last piece: cab pairings from the amp guide reach the
planner, and cab pairing is a plannable, read-back-verified action."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import server
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
SIDECAR = json.loads((ROOT / "config" / "amp_models.json").read_text())["amps"]
#: The reference's line count at 126b7b8 (ToneCommand 1.3.0), measured.
BASELINE_LINES = 741
MAX_GROWTH = 400


def test_every_amp_with_a_sidecar_pairing_carries_it_and_no_other_does():
    ref = server.PARAM_REFERENCE.splitlines()
    by_fractal = {v["fractal"]: v for v in SIDECAR.values()}
    with_pairing = [v for v in SIDECAR.values() if v.get("orig_cab") or v.get("dynacab")]
    assert with_pairing, "fixture sanity: the sidecar names cabs"
    covered = 0
    for v in with_pairing:
        line = next((l for l in ref if l.startswith(v["fractal"] + " =")), None)
        assert line is not None, v["fractal"]
        if v.get("orig_cab"):
            assert f"pairs with {v['orig_cab']}" in line
        if v.get("dynacab"):
            assert f"DynaCab {v['dynacab']}" in line
        covered += 1
    assert covered == len(with_pairing), "100 percent of sidecar pairings are on the reference"
    assert "pairs with None" not in server.PARAM_REFERENCE
    assert "DynaCab None" not in server.PARAM_REFERENCE
    for v in SIDECAR.values():
        if not (v.get("orig_cab") or v.get("dynacab")):
            line = next((l for l in ref if l.startswith(v["fractal"] + " =") or l == v["fractal"]), None)
            assert line is not None and "pairs with" not in line, v["fractal"]


def test_reference_growth_is_bounded_against_the_recorded_baseline():
    n = server.PARAM_REFERENCE.count("\n")
    assert BASELINE_LINES <= n < BASELINE_LINES + MAX_GROWTH, n
    assert "Cab pairing (issue #6)" in server.PARAM_REFERENCE
    assert "set_cab" in server.PARAM_REFERENCE.split("Cab pairing (issue #6)", 1)[1][:600]


def test_named_dynacabs_resolve_to_factory_cabs_whose_name_carries_them():
    resolved = 0
    for name in ("4x10 Bassguy RI", "4x12 Recto Straight", "4x12 1960TV", "2x12 Double Verb"):
        hit = server.resolve_dynacab(name)
        if hit is None:
            continue
        bank, ordinal, roster_name = hit
        assert str(ordinal) in server.reg.cab_rosters[str(bank)]
        head = name.split()[0].lower()
        assert head in roster_name.lower(), (name, roster_name)
        resolved += 1
    assert resolved >= 3
    assert server.resolve_dynacab("1x10 Metro Blues") is None, "no guessing when the catalog lacks it"


def test_an_amp_line_with_a_resolved_pairing_names_a_real_factory_target():
    line = next(l for l in server.PARAM_REFERENCE.splitlines() if l.startswith("59 Bassguy Bright ="))
    assert "-> factory bank 1 ordinal 194 4x10 Bassguy 57 B" in line


def test_cab_pairing_is_a_plannable_verified_write_on_the_simulator():
    dev = SimFM9(server.reg)
    dev.status_dump()
    ordn, label = server.resolve_type_ordinal("DISTORT", "59 Bassguy Bright")
    r = server.run_action(dev, server.Action(kind="set_type", block="amp", type_name=label))
    assert r["ok"], r
    bank, ordinal, name = server.resolve_dynacab("4x10 Bassguy RI")
    errs, _ = server.validate_action(server.Action(kind="set_cab", block="cab", bank=bank, value=ordinal))
    assert errs == []
    r = server.run_action(dev, server.Action(kind="set_cab", block="cab", bank=bank, value=ordinal))
    assert r["ok"] and name.split()[0] in r["detail"], r
    assert server._read_cab(dev) == (bank, ordinal)
