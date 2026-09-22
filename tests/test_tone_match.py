"""Issue #105 (G6): reference tone-match by measurement. A reference made
by EQ-ing the real capture proves the proposed direction reverses the EQ;
the moves are first steps through the plan path; nothing sends.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest
from fastapi.testclient import TestClient

import server
from fm9 import capture, measure as M, tone_match as TM
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
REAL = ROOT / "tests" / "fixtures" / "capture_test_138.wav"
UI = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
RATE = 48000


def _shelf(x: np.ndarray, rate: int, lo_hz: float, hi_hz: float, gain_db: float) -> np.ndarray:
    """Boost or cut one band of a stereo signal in the frequency domain."""
    out = np.empty_like(x)
    for c in range(x.shape[1]):
        spec = np.fft.rfft(x[:, c].astype(np.float64))
        f = np.fft.rfftfreq(len(x), 1 / rate)
        sel = (f >= lo_hz) & (f < hi_hz)
        spec[sel] *= 10 ** (gain_db / 20)
        out[:, c] = np.fft.irfft(spec, n=len(x))
    return np.clip(out, -0.99, 0.99).astype(np.float32)


@pytest.fixture
def eqd_reference(tmp_path):
    """The real capture with 6 dB more body and 6 dB less bite: a build
    matched against it has less body and more bite, so bass goes up and
    treble goes down."""
    rate, x = M.load_wav(REAL)
    y = _shelf(_shelf(x, rate, 180, 500, +6.0), rate, 6000, 10000, -6.0)
    p = tmp_path / "reference.wav"
    capture.write_wav(p, y, rate)
    return p


# --- REQ-001: match, deltas, direction ---------------------------------------------------------

def test_match_names_the_deltas_and_the_direction_reverses_the_eq(eqd_reference):
    mt = TM.match(REAL, eqd_reference, "the EQ'd reference")
    d = mt["band_deltas_db"]
    assert d["body"] < -4.5 and d["bite"] > 4.5                     # the build has less body, more bite
    # band ratios are against the whole, so a 6 dB body boost on the reference
    # nudges every other band's share by about 1.3 dB: those stay under or near
    # the 1.5 dB line and never produce a move that contradicts the EQ
    by = {m["band"]: m for m in mt["moves"]}
    assert by["body"]["param"] == "DISTORT_BASS" and by["body"]["direction"] == "up"
    assert by["bite"]["param"] == "DISTORT_TREBLE" and by["bite"]["direction"] == "down"
    assert all(abs(d[b]) < 2.0 for b in ("presence", "air", "rumble", "low_control"))
    assert not any(m["band"] in ("presence", "rumble", "low_control") for m in mt["moves"])
    assert all("than the EQ'd reference" in ln for ln in mt["lines"])
    assert by["body"]["why"].endswith("less body than the EQ'd reference: bass up")
    assert mt["status"] == "concern" and "calibration" in mt["missing_facts"][0]
    # the real capture against itself: no gap, no move
    same = TM.match(REAL, REAL)
    assert same["moves"] == [] and same["status"] == "verified"


def test_moves_use_the_declared_table_and_the_min_delta():
    rm = TM.table()
    assert rm["min_delta_db"] == 1.5 and rm["step"] == 1.0 and rm["hz_step"] == 20
    assert set(rm["knobs"]) == {"rumble", "low_control", "body", "presence", "bite", "air"}
    mv = TM.moves({"rumble": 4.0, "low_control": -2.0, "body": 1.4, "presence": -1.5, "bite": 0.2, "air": 3.0})
    got = {m["band"]: (m["param"], m["direction"], m["step"]) for m in mv}
    assert got == {"rumble": ("DISTORT_HPFREQ", "up", 20.0),     # more rumble: low cut up
                   "low_control": ("DISTORT_DEPTH", "up", 1.0),  # less low control: depth up
                   "presence": ("DISTORT_PRESENCE", "up", 1.0),  # exactly at the line counts
                   "air": ("DISTORT_LPFREQ", "down", 20.0)}      # more air: high cut down
    assert "body" not in got and "bite" not in got                 # under 1.5 dB: no move
    # every knob in the table is a real amp parameter with that name
    for band, knob in rm["knobs"].items():
        assert server.reg.spec("DISTORT", knob["pid"], 1).name == knob["param"]


def test_direction_is_a_first_step_from_the_current_knob_and_respects_the_range():
    mv = TM.moves({"body": -3.0, "bite": 2.0, "rumble": 5.0})
    acts = TM.actions(mv, {"DISTORT_BASS": 9.5, "DISTORT_TREBLE": 0.0, "DISTORT_HPFREQ": 990.0}, server.reg)
    by = {a["param"]: a for a in acts}
    assert by["DISTORT_BASS"]["value"] == 10.0 and by["DISTORT_TREBLE"]["value"] == 0.0
    assert by["DISTORT_HPFREQ"]["value"] == 1000.0
    assert all(a["kind"] == "set_param" and a["block"] == "DISTORT" for a in acts)
    assert "first step" in by["DISTORT_BASS"]["reason"] and "3.0 dB less body" in by["DISTORT_BASS"]["reason"]
    assert [m["clamped"] for m in mv] == [True, True, True]
    mv2 = TM.moves({"body": -3.0})
    acts2 = TM.actions(mv2, {}, server.reg)
    assert acts2 == [] and mv2[0]["skipped"].startswith("the knob's current value")
    src = (ROOT / "fm9" / "tone_match.py").read_text(encoding="utf-8")
    assert "set_param(" not in src and "store_preset" not in src and "urllib" not in src


# --- REQ-002: routes, plan shape, page, CLI ------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path, eqd_reference):
    caps = tmp_path / "captures"; caps.mkdir()
    refs = tmp_path / "references"; refs.mkdir()
    (caps / "build.wav").write_bytes(REAL.read_bytes())
    (refs / "song.wav").write_bytes(eqd_reference.read_bytes())
    monkeypatch.setenv("TONECOMMAND_CAPTURES", str(caps))
    monkeypatch.setenv("TONECOMMAND_REFERENCES", str(refs))
    sim = SimFM9(server.reg); sim.status_dump()
    sim.set_param_display(server.reg.spec("DISTORT", 12, 1), 5.0)
    sim.set_param_display(server.reg.spec("DISTORT", 14, 1), 6.0)
    monkeypatch.setattr(server, "_fm9", sim)
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    return TestClient(server.app), caps, refs


def test_route_lists_references_and_matches_through_the_fix_shape(client, tmp_path):
    c, caps, refs = client
    r = c.get("/api/references").json()
    assert [x["name"] for x in r["references"]] == ["song.wav"] and r["dir"] == str(refs.resolve())
    r = c.post("/api/tone-match", json={"build": str(caps / "build.wav"), "reference": str(refs / "song.wav")})
    assert r.status_code == 200, r.text
    d = r.json()
    by = {a["param"]: a for a in d["proposal"]["actions"]}
    assert by["DISTORT_BASS"]["value"] == 6.0 and by["DISTORT_TREBLE"]["value"] == 5.0
    assert d["proposal"]["fixes"][0]["how"] == "actions" and d["proposal"]["summary"].startswith("match the reference: ")
    assert len(d["proposal"]["actions"]) in (2, 3) and "first step" in d["proposal"]["summary"]
    assert all("than song" in ln for ln in d["match"]["lines"])
    # read only, both folders
    assert sorted(p.name for p in caps.iterdir()) == ["build.wav"] and sorted(p.name for p in refs.iterdir()) == ["song.wav"]
    outside = tmp_path / "elsewhere.wav"; outside.write_bytes(REAL.read_bytes())
    assert c.post("/api/tone-match", json={"build": str(outside), "reference": str(refs / "song.wav")}).status_code == 400
    assert c.post("/api/tone-match", json={"build": str(caps / "build.wav"), "reference": str(outside)}).status_code == 400
    assert c.post("/api/tone-match", json={"build": str(caps / "build.wav"), "reference": str(caps / "build.wav")}).status_code == 400
    server._gig_mode["on"] = True
    assert c.post("/api/tone-match", json={"build": str(caps / "build.wav"), "reference": str(refs / "song.wav")}).status_code == 423
    server._gig_mode["on"] = False


def test_a_reference_at_another_rate_still_matches(tmp_path):
    rate, x = M.load_wav(REAL)
    p = tmp_path / "ref441.wav"
    capture.write_wav(p, x[::1], 44100)                  # the same samples labelled 44.1 kHz: a different spectrum, still a spectrum
    mt = TM.match(REAL, p)
    assert set(mt["band_deltas_db"]) == {"rumble", "low_control", "body", "presence", "bite", "air"}


def test_the_page_matches_through_show_plan_and_the_cli_prints_the_moves(eqd_reference):
    assert 'id="matchref"' in UI and "fetch('/api/references')" in UI and "fetch('/api/tone-match'" in UI
    assert "showPlan({summary: d.proposal.summary, actions: d.proposal.actions" in UI
    py = ROOT / ".venv" / "bin" / "python"
    exe = str(py) if py.exists() else sys.executable
    out = subprocess.run([exe, str(ROOT / "tools" / "measure.py"), str(REAL), "--reference", str(eqd_reference)],
                         capture_output=True, text=True, timeout=120, env={"PATH": "/usr/bin:/bin", "TONECOMMAND_SIM": "1"})
    assert out.returncode == 0, out.stderr
    assert "less body than reference: bass up" in out.stdout and "treble down" in out.stdout
