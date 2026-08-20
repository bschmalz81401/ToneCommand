"""Drive-models sidecar: loading, drift guard, planner annotation."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from fm9.registry import Registry, DriveModelsStale

ROOT = Path(__file__).resolve().parent.parent


def test_sidecar_loads_and_maps():
    reg = Registry()
    assert len(reg.drive_models) >= 30
    assert reg.drive_model(0) == "two versions of the Pro Co RAT"
    assert "Rat Distortion = " in reg.drive_description(0)


def test_unmapped_drive_falls_back_to_fractal_name():
    reg = Registry()
    unmapped = [o for o in reg.drive_roster if o not in reg.drive_models]
    assert unmapped                     # the guide does not cover everything
    o = unmapped[0]
    assert reg.drive_description(o) == reg.drive_roster[o]   # no '=', no invention


def test_drift_guard(tmp_path):
    data = json.loads((ROOT / "config" / "drive_models.json").read_text())
    first = next(iter(data["drives"]))
    data["drives"][first]["fractal"] = "WRONG"
    bad = tmp_path / "drive_models.json"
    bad.write_text(json.dumps(data))
    reg = Registry()
    with pytest.raises(DriveModelsStale):
        reg._load_drive_models(bad)


def test_planner_reference_annotated():
    import server
    ref = server.param_reference()
    assert any(l.startswith("T808 OD = ") for l in ref.splitlines())


def test_resolver_accepts_real_pedal_name():
    import server
    got = server.resolve_type_ordinal("FUZZ", "boss sd-1 super overdrive")
    assert got and got[1] == "Super OD"
