"""#144 (I1): the .nam reader classifies from the file's own metadata and
invents nothing."""
import json
import sys
from pathlib import Path

import pytest

from fm9 import nam
from fm9.nam import NOT_ON_FILE, CaptureRecord, NamError, read_nam, read_nam_file

ROOT = Path(__file__).resolve().parent.parent


def wavenet_doc(metadata=None, **over):
    doc = {
        "version": "0.5.4",
        "architecture": "WaveNet",
        "config": {"layers": [
            {"input_size": 1, "condition_size": 1, "channels": 16, "head_size": 8, "kernel_size": 3,
             "dilations": [1, 2, 4, 8, 16, 32, 64, 128, 256, 512], "activation": "Tanh", "gated": False, "head_bias": False},
            {"input_size": 16, "condition_size": 1, "channels": 8, "head_size": 1, "kernel_size": 3,
             "dilations": [1, 2, 4, 8, 16, 32, 64, 128, 256, 512], "activation": "Tanh", "gated": False, "head_bias": True},
        ], "head": None, "head_scale": 0.02},
        "weights": [0.01, -0.02, 0.03, 0.0],
    }
    if metadata is not None:
        doc["metadata"] = metadata
    doc.update(over)
    return doc


FULL = {"date": {"year": 2026, "month": 9, "day": 19}, "name": "5150 Lead Ch",
        "modeled_by": "Moncy", "gear_type": "amp", "gear_make": "Peavey", "gear_model": "5150",
        "tone_type": "hi_gain", "input_level_dbu": 12.0, "output_level_dbu": 8.5}


def test_full_metadata_classifies_on_all_five_dimensions():
    meta = dict(FULL, date="2026-09-19")
    rec = read_nam(json.dumps(wavenet_doc(meta)).encode())
    assert isinstance(rec, CaptureRecord)
    # 1 architecture and config
    assert rec.architecture == "WaveNet" and rec.weights == 4 and rec.version == "0.5.4"
    assert rec.config_summary == "(2 layer groups, 20 layers, channels 8/16)"
    # 2 identity carried through
    assert (rec.gear_make, rec.gear_model, rec.name, rec.modeled_by, rec.date) == ("Peavey", "5150", "5150 Lead Ch", "Moncy", "2026-09-19")
    assert rec.gear == "Peavey 5150"
    # 3 cab need from gear_type
    assert rec.gear_type == "amp" and rec.needs_cab is True
    # 4 scene role from tone_type
    assert rec.tone_type == "hi_gain" and rec.scene_role == "lead"
    # 5 levels for I5
    assert rec.input_level_dbu == 12.0 and rec.output_level_dbu == 8.5
    assert rec.metadata_present and set(rec.on_file) == set(nam.METADATA_FIELDS)
    line = rec.describe()
    assert line == ("capture of Peavey 5150 by Moncy; hi_gain; needs a cab; WaveNet (2 layer groups, 20 layers, "
                    "channels 8/16); in 12.0 dBu, out 8.5 dBu")
    assert NOT_ON_FILE not in line
    d = rec.as_dict()
    assert d["needs_cab"] is True and d["scene_role"] == "lead" and d["line"] == line


@pytest.mark.parametrize("gear_type,needs_cab", [
    ("amp", True), ("pedal", True), ("pedal_amp", True), ("preamp", True),
    ("amp_cab", False), ("amp_pedal_cab", False), ("studio", False), (None, True), ("something_new", True),
])
def test_gear_type_decides_the_cab_need(gear_type, needs_cab):
    meta = dict(FULL, date="2026-09-19")
    if gear_type is None:
        meta.pop("gear_type")
    else:
        meta["gear_type"] = gear_type
    rec = read_nam(json.dumps(wavenet_doc(meta)))
    assert rec.needs_cab is needs_cab
    if gear_type is None:
        assert "needs a cab (gear type not on file)" in rec.describe()


@pytest.mark.parametrize("tone_type,role", [("clean", "clean"), ("overdrive", "rhythm"), ("crunch", "rhythm"),
                                            ("fuzz", "rhythm"), ("hi_gain", "lead"), (None, None), ("weird", None)])
def test_tone_type_maps_onto_the_standard_scene_roles(tone_type, role):
    meta = dict(FULL, date="2026-09-19")
    if tone_type is None:
        meta.pop("tone_type")
    else:
        meta["tone_type"] = tone_type
    assert read_nam(json.dumps(wavenet_doc(meta))).scene_role == role
    assert nam.TONE_TYPE_ROLE == {"clean": "clean", "overdrive": "rhythm", "crunch": "rhythm", "fuzz": "rhythm", "hi_gain": "lead"}


def test_no_metadata_block_yields_not_on_file_everywhere_and_nothing_invented():
    rec = read_nam(json.dumps(wavenet_doc()).encode())
    assert rec.metadata_present is False and rec.on_file == []
    for key in nam.METADATA_FIELDS:
        assert getattr(rec, key) is None, key
    assert rec.needs_cab is True and rec.scene_role is None and rec.gear is None
    line = rec.describe()
    assert line == ("capture of gear not on file, modeled_by not on file; tone type not on file; "
                    "needs a cab (gear type not on file); WaveNet (2 layer groups, 20 layers, channels 8/16); levels not on file")
    # nothing invented: no make, model, person, level or date appears anywhere in the record
    text = json.dumps(rec.as_dict())
    for invented in ("Peavey", "5150", "Moncy", "dBu\": 1", "2026"):
        assert invented not in text
    # an empty metadata object is present but says nothing
    rec2 = read_nam(json.dumps(wavenet_doc({})))
    assert rec2.metadata_present is True and rec2.on_file == [] and rec2.gear is None
    # a blank string is absence, a non-numeric level is absence, a dict date (the spec's other form) is absence
    rec3 = read_nam(json.dumps(wavenet_doc({"gear_make": "  ", "input_level_dbu": "loud", "date": {"year": 2026}})))
    assert rec3.gear_make is None and rec3.input_level_dbu is None and rec3.date is None and rec3.on_file == []


def test_lstm_and_unknown_configs_are_summarised_from_their_own_keys():
    lstm = wavenet_doc(architecture="LSTM", config={"hidden_size": 24, "num_layers": 1, "input_size": 1})
    assert read_nam(json.dumps(lstm)).config_summary == "(hidden size 24, layers 1)"
    odd = wavenet_doc(architecture="ConvNet", config={"channels": 16, "dilations": [1, 2]})
    assert read_nam(json.dumps(odd)).config_summary == "(config keys: channels, dilations)"
    nocfg = wavenet_doc(); nocfg.pop("config")
    assert read_nam(json.dumps(nocfg)).config_summary == "config not on file"


@pytest.mark.parametrize("data,reason", [
    (b"{not json", "invalid JSON"),
    (b"[]", "the top level is not a JSON object"),
    (json.dumps({"architecture": "WaveNet", "config": {}}).encode(), "no weights list"),
    (json.dumps({"config": {}, "weights": [0.1]}).encode(), "no architecture"),
    (json.dumps({"architecture": "WaveNet", "weights": "0.1 0.2"}).encode(), "no weights list"),
    (b"\xff\xfe\x00", "not UTF-8 text"),
])
def test_malformed_input_is_refused_with_one_line_and_nothing_else_raised(data, reason):
    with pytest.raises(NamError) as exc:
        read_nam(data)
    assert reason in str(exc.value) and "\n" not in str(exc.value)


def test_read_from_a_path_and_a_missing_file(tmp_path):
    p = tmp_path / "amp.nam"
    p.write_text(json.dumps(wavenet_doc(dict(FULL, date="2026-09-19"))), encoding="utf-8")
    assert read_nam_file(p).gear == "Peavey 5150"
    with pytest.raises(NamError) as exc:
        read_nam_file(tmp_path / "missing.nam")
    assert "cannot read missing.nam" in str(exc.value)


def test_the_reader_imports_nothing_beyond_the_standard_library():
    src = (ROOT / "fm9" / "nam.py").read_text(encoding="utf-8")
    imports = [l.strip() for l in src.splitlines() if l.startswith(("import ", "from "))]
    assert imports == ["from __future__ import annotations", "import json",
                       "from dataclasses import dataclass, field", "from pathlib import Path"]
    assert "torch" not in src and "neural_amp_modeler" not in src
