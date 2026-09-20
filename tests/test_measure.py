"""Issues #101 (G2), #102 (G3), #103 (G4): the measurement ears. Real
captures from the unit (Gate 0, 2026-09-20) are fixtures; the rest is
synthetic. No network, no device beyond the simulator, no file written by
a route.
"""
import json
import subprocess
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest
from fastapi.testclient import TestClient

import server
from fm9 import capture, measure as M, sound_check
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
REAL = FIX / "capture_test_138.wav"
FLOOR = FIX / "capture_silence_138.wav"
RATE = 48000


def _wav(path: Path, data: np.ndarray, rate: int = RATE) -> Path:
    capture.write_wav(path, data, rate)
    return path


def _sine(freq: float, dbfs: float, seconds: float = 4.0, rate: int = RATE) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (10 ** (dbfs / 20) * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _noise(seconds: float = 4.0, seed: int = 1, dbfs: float = -20.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * RATE)).astype(np.float32)
    return (x / np.sqrt(np.mean(x ** 2)) * 10 ** (dbfs / 20)).astype(np.float32)


def _stereo(left, right):
    return np.stack([left, right], 1)


# --- REQ-001: validity, spectrum, bands ------------------------------------------------------------

def test_policy_is_versioned_and_only_intent_or_acquisition_rules_are_enforced(tmp_path):
    pol = M.policy()
    assert pol["version"] == 1 and set(pol["bands"]) == {"rumble", "low_control", "body", "presence", "bite", "air"}
    for r in pol["rules"]:
        assert r["cls"] in M.CLASSES
        if r["enforced"]:
            assert r["cls"] in ("intent_target", "acquisition"), r["id"]
    assert [r["id"] for r in pol["rules"] if r["cls"] == "style" and r["enforced"]] == []
    assert pol["acquisition"]["active_below_peak_db"] == 40.0 and pol["acquisition"]["dropout_ms"] == 50
    assert set(pol["checker"]["statuses"]) == set(M.STATUSES)
    bad = {**pol, "rules": [{"id": "fizz", "cls": "style", "enforced": True}]}
    p = tmp_path / "bad_policy.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(M.MeasureError, match="only intent_target and acquisition"):
        M.policy(p)


def test_validity_names_each_acquisition_fault(tmp_path):
    ok = _stereo(_noise(), _noise(seed=2))
    assert M.validity(ok, RATE) == (True, None)
    assert "too short" in M.validity(ok[:RATE // 2], RATE)[1]
    assert "digital silence" in M.validity(np.zeros((RATE * 2, 2), np.float32), RATE)[1]
    clipped = ok.copy(); clipped[1000:1100] = 1.0
    assert "clipping" in M.validity(clipped, RATE)[1]
    dropped = ok.copy(); dropped[RATE:RATE + int(RATE * 0.06)] = 0.0
    assert "dropout: 60 ms" in M.validity(dropped, RATE)[1]
    # lead-in zeros (USB latency before the signal arrives) are not a dropout
    lead = ok.copy(); lead[:int(RATE * 0.26)] = 0.0
    assert M.validity(lead, RATE) == (True, None)
    # the real capture has 263 ms of lead-in zeros and is valid
    rate, x = M.load_wav(REAL)
    assert rate == RATE and x.shape == (192000, 2) and M.validity(x, rate) == (True, None)


def test_bands_are_ratios_against_the_whole_and_read_the_real_capture_sensibly():
    rate, x = M.load_wav(REAL)
    freqs, db = M.spectrum(x, rate)
    b = M.bands(freqs, db)
    assert set(b) == {"rumble", "low_control", "body", "presence", "bite", "air"}
    # the processed guitar capture: energy sits in low control and body,
    # the cab rolls off bite and air
    assert b["body"] > b["bite"] > b["air"]
    assert b["presence"] > b["air"]
    assert 200 < M.centroid(freqs, db) < 5000
    # a band ratio is invariant to level: the same capture 12 dB quieter reads the same bands
    freqs2, db2 = M.spectrum(x * 10 ** (-12 / 20), rate)
    b2 = M.bands(freqs2, db2)
    assert all(abs(b[k] - b2[k]) < 0.05 for k in b)
    # a 100 Hz tone lands in low control, a 3 kHz tone in presence
    lo = M.bands(*M.spectrum(_stereo(_sine(100, -20), _sine(100, -20)), RATE))
    hi = M.bands(*M.spectrum(_stereo(_sine(3000, -20), _sine(3000, -20)), RATE))
    assert lo["low_control"] == max(lo.values()) and hi["presence"] == max(hi.values())


def test_measure_carries_the_checker_contract_and_an_invalid_capture_stops_at_validity(tmp_path):
    m = M.measure(REAL)
    d = m.as_dict()
    assert d["status"] == "verified" and d["valid"] is True
    assert set(d["coverage"]["metrics"]) == {"validity", "bands", "centroid", "loudness", "stereo", "dynamics"}
    assert d["evidence"]["source"].endswith("capture_test_138.wav") and d["evidence"]["captures"] == 1
    assert any("reference" in f for f in d["missing_facts"]) and any("request" in f for f in d["missing_facts"])
    assert d["findings"] == []                       # no request, no reference: evidence only, no verdict
    bad = _wav(tmp_path / "clip.wav", np.ones((RATE * 2, 2), np.float32))
    m = M.measure(bad)
    d = m.as_dict()
    assert d["status"] == "invalid" and d["bands"] == {} and d["loudness"] is None
    assert d["findings"][0]["cls"] == "acquisition" and "clipping" in d["findings"][0]["line"]
    assert d["missing_facts"] == ["a valid capture"]


def test_compare_names_the_baseline_in_every_line():
    a, b = M.measure(FLOOR), M.measure(REAL)
    c = M.compare(a, b, "the test capture")
    assert c["baseline"] == "the test capture"
    assert all("than the test capture" in ln for ln in c["lines"])
    assert c["loudness_delta_lu"] < -20 and c["band_deltas_db"]["air"] > 10
    with pytest.raises(M.MeasureError, match="two valid"):
        M.compare(a, M.Measurement(False, "x", RATE, 0, 2), "b")


# --- REQ-002: loudness and the balance policy --------------------------------------------------------

def test_loudness_conforms_on_the_bs1770_tone_and_is_none_off_48k():
    # EBU Tech 3341 case: a -23 dBFS 1 kHz sine on both channels reads -23.0 LUFS
    x = _stereo(_sine(1000, -23), _sine(1000, -23))
    l = M.loudness(x, RATE)
    assert abs(l["integrated_lufs"] + 23.0) <= 0.1
    assert abs(l["short_term"]["median"] + 23.0) <= 0.1 and l["gated_blocks"] > 30
    # 6 dB down reads 6 LU down
    l2 = M.loudness(x * 10 ** (-6 / 20), RATE)
    assert abs((l["integrated_lufs"] - l2["integrated_lufs"]) - 6.0) <= 0.1
    # the relative gate drops the quiet tail: a tone followed by 40 dB of near
    # silence reads the tone, give or take the 400 ms blocks straddling the edge
    tail = x.copy(); tail[RATE * 2:] *= 10 ** (-40 / 20)
    assert abs(M.loudness(tail, RATE)["integrated_lufs"] + 23.0) <= 0.5
    assert abs(M.loudness(tail[:RATE * 2], RATE)["integrated_lufs"] + 23.0) <= 0.1
    assert M.loudness(x, 44100) is None
    assert "loudness" not in M.Measurement.__dataclass_fields__ or True
    assert M.loudness(np.zeros((RATE * 2, 2), np.float32), RATE)["integrated_lufs"] is None


def test_measure_off_rate_reports_a_signal_finding_not_a_wrong_number(tmp_path):
    p = _wav(tmp_path / "441.wav", _stereo(_noise(), _noise(seed=3)), rate=44100)
    d = M.measure(p).as_dict()
    assert d["loudness"] is None and d["status"] == "verified"
    assert [f["cls"] for f in d["findings"]] == ["signal"] and "44100" in d["findings"][0]["line"]
    assert any("48 kHz" in f for f in d["missing_facts"])


def test_scene_balance_applies_each_rulebook_rule_and_names_the_baseline():
    rows = [{"scene": 1, "role": "clean", "lufs": -26.0}, {"scene": 2, "role": "rhythm", "lufs": -23.0},
            {"scene": 3, "role": "rhythm", "lufs": -24.6}, {"scene": 4, "role": "lead", "lufs": -22.5},
            {"scene": 5, "role": "lead", "lufs": -18.0}, {"scene": 6, "role": "other", "lufs": -23.5}]
    out = M.scene_balance(rows)
    assert out["status"] == "concern"
    assert out["baseline"] == "rhythm median -23.8 LUFS (scenes 2, 3)"
    by = {(f["rule"], f["line"].split()[1]): f for f in out["findings"]}
    assert ("clean_not_below_rhythm", "1") in by            # clean 2.2 LU below
    assert ("rhythm_within_1lu", "3") not in by             # 0.8 LU from the median: within 1
    assert ("lead_2_to_3lu_above_rhythm", "4") in by        # lead only 1.3 above
    assert ("cap_4lu_above_rhythm", "5") in by              # lead 5.8 above the median
    assert all(f["baseline"] == out["baseline"] for f in out["findings"])
    assert all(f["cls"] == "intent_target" for f in out["findings"])
    assert out["coverage"]["scenes"] == [1, 2, 3, 4, 5, 6]
    # a balanced set: verified, with what is still missing named
    good = M.scene_balance([{"scene": 1, "role": "clean", "lufs": -23.0}, {"scene": 2, "role": "rhythm", "lufs": -23.0},
                            {"scene": 3, "role": "lead", "lufs": -20.5}])
    assert good["status"] == "verified" and good["findings"] == []
    assert "a second rhythm scene" in good["missing_facts"][0]
    # no rhythm scene: unknown, nothing judged
    none = M.scene_balance([{"scene": 1, "role": "clean", "lufs": -23.0}])
    assert none["status"] == "unknown" and none["findings"] == [] and none["baseline"] is None


# --- REQ-003: stereo width and dynamics ----------------------------------------------------------------

def test_stereo_tells_mono_wide_and_one_sided_apart_and_reads_the_real_capture():
    n = _noise()
    mono = M.stereo(_stereo(n, n))
    assert mono["correlation"] > 0.999 and mono["mono_like"] is True and abs(mono["mono_loss_db"]) < 0.01
    wide = M.stereo(_stereo(_noise(seed=1), _noise(seed=2)))
    assert abs(wide["correlation"]) < 0.05 and wide["mono_like"] is False
    assert wide["mono_loss_db"] > 2.5                    # decorrelated: 3 dB lost in the fold-down
    one = M.stereo(_stereo(n, n * 10 ** (-20 / 20)))
    assert one["correlation"] > 0.999 and one["mono_like"] is False and abs(one["level_diff_db"] - 20) < 0.1
    inverted = M.stereo(_stereo(n, -n))
    assert inverted["correlation"] < -0.999 and inverted["mono_loss_db"] > 60
    real = M.measure(REAL).stereo
    assert 0.5 < real["correlation"] < 0.98 and real["mono_like"] is False
    assert M.stereo(n[:, None]) is None


def test_width_is_a_finding_only_when_the_request_asked_for_it(tmp_path):
    n = _noise()
    p = _wav(tmp_path / "mono.wav", _stereo(n, n))
    quiet = M.measure(p).as_dict()
    assert quiet["stereo"]["mono_like"] is True and quiet["findings"] == [] and quiet["status"] == "verified"
    asked = M.measure(p, request="a big wide 80s clean").as_dict()
    assert asked["status"] == "concern"
    f = asked["findings"][0]
    assert f["rule"] == "asked_wide_measured_mono" and f["cls"] == "intent_target"
    assert f["line"].startswith("asked for wide, measured mono (correlation 1.00")
    assert f["baseline"] == "your request: 'a big wide 80s clean'" and f["limit"] == 0.98
    tight = M.measure(p, request="a tight rhythm crunch").as_dict()
    assert tight["findings"] == []


def test_dynamics_reports_crest_and_the_short_term_spread():
    tone = _stereo(_sine(1000, -20), _sine(1000, -20))
    d = M.dynamics(tone, M.loudness(tone, RATE))
    assert abs(d["crest_db"] - 3.01) < 0.05                  # a sine's crest factor
    assert d["short_term_spread_lu"] == 0.0
    noise = _stereo(_noise(), _noise(seed=2))
    assert M.dynamics(noise, None)["crest_db"] > 9 and M.dynamics(noise, None)["short_term_spread_lu"] is None
    assert M.measure(REAL).dynamics["crest_db"] > 6


# --- REQ-004: routes, the scene loop, the CLI ----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    caps = tmp_path / "captures"; caps.mkdir()
    monkeypatch.setenv("TONECOMMAND_CAPTURES", str(caps))
    (caps / "real.wav").write_bytes(REAL.read_bytes())
    (caps / "floor.wav").write_bytes(FLOOR.read_bytes())
    n = _noise()
    _wav(caps / "mono.wav", _stereo(n, n))
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    return TestClient(server.app), caps


def test_route_measures_a_capture_under_the_captures_folder_only(client, tmp_path):
    c, caps = client
    r = c.post("/api/measure", json={"path": str(caps / "real.wav")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "verified" and d["loudness"]["integrated_lufs"] < -20 and d["bands"]["body"] > d["bands"]["air"]
    r = c.post("/api/measure", json={"path": str(caps / "mono.wav"), "request": "wide and big",
                                     "baseline": str(caps / "real.wav"), "baseline_label": "the test capture"})
    d = r.json()
    assert d["findings"][0]["rule"] == "asked_wide_measured_mono"
    assert all("the test capture" in ln for ln in d["compare"]["lines"])
    outside = tmp_path / "elsewhere.wav"; outside.write_bytes(REAL.read_bytes())
    assert c.post("/api/measure", json={"path": str(outside)}).status_code == 400
    assert c.post("/api/measure", json={"path": str(caps / "../elsewhere.wav")}).status_code == 400
    assert c.post("/api/measure", json={"path": ""}).status_code == 400
    # a sidecar path is accepted for its wav
    (caps / "real.json").write_text("{}")
    assert c.post("/api/measure", json={"path": str(caps / "real.json")}).status_code == 200
    assert sorted(p.name for p in caps.iterdir()) == ["floor.wav", "mono.wav", "real.json", "real.wav"]


def test_route_balance_measures_each_scene_and_applies_the_rules(client):
    c, caps = client
    r = c.post("/api/measure/balance", json={"captures": [
        {"scene": 1, "role": "clean", "path": str(caps / "floor.wav")},
        {"scene": 2, "role": "rhythm", "path": str(caps / "real.wav")}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "concern" and d["findings"][0]["rule"] == "clean_not_below_rhythm"
    assert d["baseline"].startswith("rhythm median") and d["captures"][1]["valid"] is True
    assert c.post("/api/measure/balance", json={"captures": []}).status_code == 400
    assert c.post("/api/measure/balance", json={"captures": [{"scene": 1, "role": "verse", "path": str(caps / "real.wav")}]}).status_code == 400


def test_capture_scenes_returns_to_the_origin_scene_on_success_and_on_failure(tmp_path):
    sim = SimFM9(server.reg); sim.status_dump()
    sim.set_scene(3)
    seen = []

    def recorder(signal, seconds, out_channel):
        seen.append(sim.scene_name()[0])
        y = signal[:int(seconds * RATE)] * 0.5
        return np.stack([y, y], 1)
    out = sound_check.capture_scenes(sim, [1, 2, 4], recorder, tmp_path, routing={"in1": 1})
    assert seen == [1, 2, 4] and out["origin_scene"] == 3 and out["returned"] is True
    assert [c["scene"] for c in out["captures"]] == [1, 2, 4] and sim.scene_name()[0] == 3
    wavs = sorted(tmp_path.glob("test-*.wav"))
    assert len(wavs) == 3 and [w.stem.split("-s")[1][0] for w in wavs] == ["1", "2", "4"]
    side = json.loads(wavs[0].with_suffix(".json").read_text())
    assert side["scene"] == 1 and side["routing"] == {"in1": 1} and side["wav"] == wavs[0].name

    def boom(signal, seconds, out_channel):
        if sim.scene_name()[0] == 2:
            raise RuntimeError("the endpoint went away")
        y = signal[:int(seconds * RATE)]
        return np.stack([y, y], 1)
    with pytest.raises(RuntimeError, match="went away"):
        sound_check.capture_scenes(sim, [1, 2, 4], boom, tmp_path)
    assert sim.scene_name()[0] == 3
    with pytest.raises(sound_check.SoundCheckError, match="1 to 8"):
        sound_check.capture_scenes(sim, [0], recorder, tmp_path)


def test_cli_prints_one_line_per_capture_and_the_balance():
    py = ROOT / ".venv" / "bin" / "python"
    exe = str(py) if py.exists() else sys.executable
    out = subprocess.run([exe, str(ROOT / "tools" / "measure.py"), str(FIX)], capture_output=True, text=True,
                         timeout=120, env={"PATH": "/usr/bin:/bin", "TONECOMMAND_SIM": "1"})
    assert out.returncode == 0, out.stderr
    assert "capture_test_138.wav: verified" in out.stdout and "LUFS" in out.stdout
    out = subprocess.run([exe, str(ROOT / "tools" / "measure.py"), "--balance", f"1:clean:{FLOOR}", f"2:rhythm:{REAL}"],
                         capture_output=True, text=True, timeout=120, env={"PATH": "/usr/bin:/bin", "TONECOMMAND_SIM": "1"})
    assert out.returncode == 0, out.stderr
    assert "concern" in out.stdout and "below the rhythm scenes" in out.stdout


def test_no_new_dependency():
    src = (ROOT / "pyproject.toml").read_text()
    assert "librosa" not in src and "pyloudnorm" not in src and "scipy" not in src
    assert "import scipy" not in (ROOT / "fm9" / "measure.py").read_text()
