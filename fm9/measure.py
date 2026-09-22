"""The measurement ears (issues #101 G2, #102 G3, #103 G4): what a capture
of the unit's output actually contains, in numbers with a named baseline.

Everything here works on a WAV the capture module wrote (48 kHz, stereo,
16-bit) and needs numpy only. Validity comes first: a truncated, silent,
clipped or dropped-out capture is an INVALID measurement, not a finding
about the tone (docs/SOUND-CHECK-DESIGN.md 3.4, acquisition class). Then:

- spectrum as band ratios over six descriptive regions (design 10.2),
  each a dB offset from the whole; a centroid as a secondary number;
- loudness per ITU-R BS.1770-4 (K-weighting, 400 ms gated blocks,
  integrated) plus a short-term distribution; no LRA on a short clip
  (design 10.3);
- stereo: L/R correlation, mid/side ratio, inter-channel level, mono
  fold-down loss (design 10.4);
- dynamics: crest factor and the short-term loudness spread.

Which of those become FINDINGS is the policy file's call
(config/sound_policy.json): only the balance rules the rulebook states
with a number, and the definitional mono check, are enforced. Nothing here
turns prose into pass/fail, and a band number is evidence next to the
baseline it was measured against, never a verdict on its own. That is the
#50 lesson (config/tone_targets.json): absolute floors were tested against
professional presets and refuted.
"""
from __future__ import annotations

import json
import math
import wave
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from fm9.paths import resource_path

import numpy as np

RATE = 48000
POLICY_PATH = resource_path("config", "sound_policy.json")
CLASSES = ("hardware_safety", "acquisition", "signal", "device", "intent_target", "style")

#: The acquisition numbers come from the policy file's `acquisition` block
#: (declared, versioned); these are the defaults for a policy without one.
MIN_SECONDS = 1.0
SILENCE_DBFS = -80.0          # digital silence: below this RMS nothing was captured
CLIP_DBFS = -0.1              # a peak at or above this is clipping at the endpoint
DROPOUT_MS = 50               # a run of exact zeros this long inside active audio
ACTIVE_WINDOW_MS = 100
ACTIVE_BELOW_PEAK_DB = 40.0   # a window this far under the loudest one is not active
STATUSES = ("verified", "concern", "unknown", "invalid", "not_applicable")
FFT = 8192
SMOOTH_OCTAVE = 1 / 6


class MeasureError(ValueError):
    """One line, written for the person reading the result."""


# --- policy -----------------------------------------------------------------------

def policy(path: Path | None = None) -> dict:
    doc = json.loads((path or POLICY_PATH).read_text(encoding="utf-8"))
    if doc.get("version") != 1 or not isinstance(doc.get("rules"), list):
        raise MeasureError("sound policy file is not version 1")
    for r in doc["rules"]:
        if r.get("cls") not in CLASSES:
            raise MeasureError(f"sound policy rule {r.get('id')!r} has no class")
        if r.get("enforced") and r["cls"] not in ("intent_target", "acquisition"):
            raise MeasureError(f"sound policy rule {r.get('id')!r} is {r['cls']} and enforced; "
                               "only intent_target and acquisition rules may be")
    return doc


def _acq(pol: dict | None, key: str, default):
    return (pol or {}).get("acquisition", {}).get(key, default)


def _rule(pol: dict, rule_id: str) -> dict:
    for r in pol["rules"]:
        if r["id"] == rule_id:
            return r
    raise MeasureError(f"no rule {rule_id!r} in the sound policy")


# --- files ---------------------------------------------------------------------------

def load_wav(path: str | Path) -> tuple[int, np.ndarray]:
    """(rate, float32 array frames x channels) from a 16-bit PCM WAV."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise MeasureError(f"{Path(path).name}: not 16-bit PCM")
        rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = w.readframes(n)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return rate, x.reshape(-1, ch)


def dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64))))) if x.size else 0.0
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def peak_dbfs(x: np.ndarray) -> float:
    pk = float(np.max(np.abs(x))) if x.size else 0.0
    return 20 * math.log10(pk) if pk > 0 else -math.inf


# --- validity ---------------------------------------------------------------------------

@dataclass
class Finding:
    rule: str
    cls: str
    line: str
    baseline: str
    value: float
    limit: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def active_windows(x: np.ndarray, rate: int, pol: dict | None = None) -> np.ndarray:
    """Boolean per window (policy acquisition.active_window_ms, 100 ms):
    active when its RMS is within acquisition.active_below_peak_db (40 dB)
    of the loudest window's. Measurement runs on active audio only; no
    active window at all means nothing was captured (digital silence)."""
    mono = x.mean(axis=1) if x.ndim == 2 else x
    n = max(1, int(rate * _acq(pol, "active_window_ms", ACTIVE_WINDOW_MS) / 1000))
    count = len(mono) // n
    if count == 0:
        return np.zeros(0, dtype=bool)
    w = mono[:count * n].reshape(count, n)
    rms = np.sqrt(np.mean(np.square(w.astype(np.float64)), axis=1))
    top = float(rms.max()) if rms.size else 0.0
    if top <= 0:
        return np.zeros(count, dtype=bool)
    return rms >= top * 10 ** (-_acq(pol, "active_below_peak_db", ACTIVE_BELOW_PEAK_DB) / 20)


def validity(x: np.ndarray, rate: int, pol: dict | None = None) -> tuple[bool, str | None]:
    """(valid, reason). Acquisition faults, in the order they are checked,
    each against the policy file's declared acquisition numbers."""
    min_s, sil, clip = (_acq(pol, "min_seconds", MIN_SECONDS), _acq(pol, "silence_dbfs", SILENCE_DBFS),
                        _acq(pol, "clip_dbfs", CLIP_DBFS))
    seconds = len(x) / rate if rate else 0
    if seconds < min_s:
        return False, f"too short: {seconds:.2f} s, needs {min_s:g} s"
    if dbfs(x) < sil:
        return False, f"digital silence: {dbfs(x):.1f} dBFS RMS; nothing was captured"
    if peak_dbfs(x) >= clip:
        return False, f"clipping at the USB endpoint: peak {peak_dbfs(x):.2f} dBFS"
    active = active_windows(x, rate, pol)
    if not active.any():
        return False, "no active audio: every window is at digital zero"
    if active.any():
        n = int(rate * _acq(pol, "active_window_ms", ACTIVE_WINDOW_MS) / 1000)
        first, last = int(np.argmax(active)) * n, (len(active) - int(np.argmax(active[::-1]))) * n
        mono = (x.mean(axis=1) if x.ndim == 2 else x)[first:last]
        zero = mono == 0
        run = int(rate * _acq(pol, "dropout_ms", DROPOUT_MS) / 1000)
        if zero.size >= run:
            # longest run of exact zeros inside the active span
            edges = np.diff(np.concatenate([[0], zero.astype(np.int8), [0]]))
            starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0]
            # a run touching either edge of the active span is lead-in (USB
            # playback latency before the signal arrives) or tail, not a
            # dropout; a dropout is silence with signal on BOTH sides
            inside = [(e - st) for st, e in zip(starts, ends) if st > 0 and e < len(mono)]
            if inside and max(inside) >= run:
                ms = int(max(inside) / rate * 1000)
                return False, f"dropout: {ms} ms of exact zeros inside active audio"
    return True, None


# --- spectrum ------------------------------------------------------------------------------

def spectrum(x: np.ndarray, rate: int, pol: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(freqs, dB) Welch-averaged magnitude over active windows with
    1/6-octave log smoothing. Mono mix; unweighted."""
    mono = (x.mean(axis=1) if x.ndim == 2 else x).astype(np.float64)
    active = active_windows(x, rate, pol)
    n = int(rate * _acq(pol, "active_window_ms", ACTIVE_WINDOW_MS) / 1000)
    keep = np.concatenate([mono[i * n:(i + 1) * n] for i in range(len(active)) if active[i]]) \
        if active.any() else mono
    if len(keep) < FFT:
        keep = np.pad(keep, (0, FFT - len(keep)))
    win = np.hanning(FFT)
    hop = FFT // 2
    acc = np.zeros(FFT // 2 + 1)
    count = 0
    for start in range(0, len(keep) - FFT + 1, hop):
        seg = keep[start:start + FFT] * win
        acc += np.abs(np.fft.rfft(seg)) ** 2
        count += 1
    power = acc / max(1, count)
    freqs = np.fft.rfftfreq(FFT, 1 / rate)
    # log-frequency smoothing: mean power over +-1/12 octave around each bin
    smoothed = np.empty_like(power)
    lo_f = freqs * 2 ** (-SMOOTH_OCTAVE / 2)
    hi_f = freqs * 2 ** (SMOOTH_OCTAVE / 2)
    lo_i = np.searchsorted(freqs, lo_f)
    hi_i = np.maximum(np.searchsorted(freqs, hi_f, side="right"), lo_i + 1)
    csum = np.concatenate([[0.0], np.cumsum(power)])
    smoothed = (csum[hi_i] - csum[lo_i]) / (hi_i - lo_i)
    db = 10 * np.log10(np.maximum(smoothed, 1e-20))
    return freqs, db


def bands(freqs: np.ndarray, db: np.ndarray, pol: dict | None = None) -> dict[str, float]:
    """Each policy band's mean dB minus the whole (20 Hz to 20 kHz) mean dB:
    a ratio, so the number says where the energy sits, not how loud."""
    pol = pol or policy()
    power = 10 ** (db / 10)
    whole = (freqs >= 20) & (freqs <= 20000)
    whole_db = 10 * math.log10(max(float(power[whole].mean()), 1e-20))
    out = {}
    for name, (lo, hi) in pol["bands"].items():
        sel = (freqs >= lo) & (freqs < hi)
        if not sel.any():
            out[name] = float("nan")
            continue
        out[name] = round(10 * math.log10(max(float(power[sel].mean()), 1e-20)) - whole_db, 2)
    return out


def centroid(freqs: np.ndarray, db: np.ndarray) -> float:
    power = 10 ** (db / 10)
    sel = (freqs >= 20) & (freqs <= 20000)
    p = power[sel]
    return round(float((freqs[sel] * p).sum() / max(p.sum(), 1e-20)), 1)


# --- loudness, BS.1770-4 ------------------------------------------------------------------------

#: K-weighting at 48 kHz, ITU-R BS.1770-4 table 1 and 2 (b0, b1, b2, a1, a2).
_K_STAGE1 = (1.53512485958697, -2.69169618940638, 1.19839281085285,
             -1.69065929318241, 0.73248077421585)
_K_STAGE2 = (1.0, -2.0, 1.0, -1.99004745483398, 0.99007225036621)
ABSOLUTE_GATE = -70.0
RELATIVE_GATE = -10.0
BLOCK_S = 0.4
BLOCK_STEP_S = 0.1
SHORT_TERM_S = 3.0


def _biquad(x: np.ndarray, coef: tuple) -> np.ndarray:
    """Direct form II transposed, one channel. A plain loop: numpy has no
    IIR and scipy is not a dependency; 200k samples take well under a
    second."""
    b0, b1, b2, a1, a2 = coef
    y = np.empty_like(x, dtype=np.float64)
    s1 = s2 = 0.0
    xs = x.astype(np.float64).tolist()
    out = y.tolist()
    for i, xi in enumerate(xs):
        yi = b0 * xi + s1
        s1 = b1 * xi - a1 * yi + s2
        s2 = b2 * xi - a2 * yi
        out[i] = yi
    return np.asarray(out)


def k_weight(x: np.ndarray) -> np.ndarray:
    chans = []
    for c in range(x.shape[1]):
        chans.append(_biquad(_biquad(x[:, c], _K_STAGE1), _K_STAGE2))
    return np.stack(chans, axis=1)


def _block_loudness(kw: np.ndarray, rate: int, block_s: float, step_s: float) -> np.ndarray:
    n, step = int(block_s * rate), int(step_s * rate)
    if len(kw) < n:
        return np.zeros(0)
    starts = range(0, len(kw) - n + 1, step)
    out = []
    for s in starts:
        z = np.mean(np.square(kw[s:s + n]), axis=0).sum()      # G = 1.0 for L and R
        out.append(-0.691 + 10 * math.log10(max(float(z), 1e-20)))
    return np.asarray(out)


def loudness(x: np.ndarray, rate: int) -> dict | None:
    """Integrated LUFS with the two-stage gate, plus the short-term
    distribution. None at any rate but 48 kHz (the filter is specified
    there; the caller adds the signal-class finding)."""
    if rate != RATE:
        return None
    if x.ndim == 1:
        x = x[:, None]
    kw = k_weight(x)
    blocks = _block_loudness(kw, rate, BLOCK_S, BLOCK_STEP_S)
    above = blocks[blocks > ABSOLUTE_GATE]
    if above.size == 0:
        integrated = None
        gated = 0
    else:
        mean_abs = 10 * math.log10(np.mean(10 ** ((above + 0.691) / 10))) - 0.691
        keep = above[above > mean_abs + RELATIVE_GATE]
        gated = int(keep.size)
        integrated = (round(10 * math.log10(np.mean(10 ** ((keep + 0.691) / 10))) - 0.691, 2)
                      if keep.size else None)
    st = _block_loudness(kw, rate, SHORT_TERM_S, BLOCK_STEP_S)
    st = st[st > ABSOLUTE_GATE]
    short = ({"min": round(float(st.min()), 2), "median": round(float(np.median(st)), 2),
              "max": round(float(st.max()), 2)} if st.size else None)
    return {"integrated_lufs": integrated, "short_term": short, "gated_blocks": gated}


# --- stereo and dynamics ---------------------------------------------------------------------------

def stereo(x: np.ndarray, pol: dict | None = None) -> dict | None:
    if x.ndim != 2 or x.shape[1] != 2:
        return None
    pol = pol or policy()
    left, right = x[:, 0].astype(np.float64), x[:, 1].astype(np.float64)
    nl, nr = np.linalg.norm(left), np.linalg.norm(right)
    corr = float(np.dot(left, right) / (nl * nr)) if nl > 0 and nr > 0 else 0.0
    mid, side = (left + right) / 2, (left - right) / 2
    mid_e, side_e = float(np.mean(mid ** 2)), float(np.mean(side ** 2))
    mid_side_db = 10 * math.log10(max(mid_e, 1e-20) / max(side_e, 1e-20))
    level_diff = dbfs(left) - dbfs(right) if nl > 0 and nr > 0 else 0.0
    lr_e = (float(np.mean(left ** 2)) + float(np.mean(right ** 2))) / 2
    mono_loss = 10 * math.log10(max(lr_e, 1e-20) / max(mid_e, 1e-20))   # positive = loss in mono
    r = _rule(pol, "mono_like")
    mono_like = corr >= float(r["number"]) and abs(level_diff) <= float(r.get("number_level_db", 0.5))
    return {"correlation": round(corr, 4), "mid_side_db": round(mid_side_db, 2),
            "level_diff_db": round(level_diff, 2), "mono_loss_db": round(mono_loss, 2),
            "mono_like": bool(mono_like)}


def dynamics(x: np.ndarray, loud: dict | None) -> dict:
    crest = peak_dbfs(x) - dbfs(x)
    spread = None
    if loud and loud.get("short_term"):
        spread = round(loud["short_term"]["max"] - loud["short_term"]["min"], 2)
    return {"crest_db": round(crest, 2), "short_term_spread_lu": spread}


# --- one capture ----------------------------------------------------------------------------------------

@dataclass
class Measurement:
    """One capture, with the design 3.5 checker contract: status,
    coverage, evidence and missing_facts travel with the numbers, so an
    empty findings list can never be read as a pass on its own."""
    valid: bool
    invalid_reason: str | None
    rate: int
    frames: int
    channels: int
    status: str = "unknown"
    coverage: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    missing_facts: list[str] = field(default_factory=list)
    bands: dict = field(default_factory=dict)
    centroid_hz: float | None = None
    loudness: dict | None = None
    stereo: dict | None = None
    dynamics: dict | None = None
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["findings"] = [f.as_dict() for f in self.findings]
        return d


def measure(path: str | Path, request: str | None = None, pol: dict | None = None) -> Measurement:
    """Everything about one capture. `request` is the player's words, if
    any: the only thing that turns a width reading into an intent finding."""
    pol = pol or policy()
    rate, x = load_wav(path)
    ok, why = validity(x, rate, pol)
    m = Measurement(ok, why, rate, int(len(x)), int(x.shape[1]))
    m.evidence = {"source": str(path), "captures": 1,
                  "repeatability": "single capture; no repeat interval measured"}
    if not ok:
        m.status = "invalid"
        m.coverage = {"metrics": [], "request": bool(request)}
        m.missing_facts = ["a valid capture"]
        m.findings.append(Finding("acquisition_invalid", "acquisition", f"invalid measurement: {why}",
                                  "the capture itself", 0.0))
        return m
    freqs, db = spectrum(x, rate, pol)
    m.bands = bands(freqs, db, pol)
    m.centroid_hz = centroid(freqs, db)
    m.loudness = loudness(x, rate)
    if m.loudness is None:
        m.findings.append(Finding("rate_not_48k", "signal",
                                  f"loudness not measured: {rate} Hz, the K-weighting is specified at 48 kHz",
                                  "the capture itself", float(rate), float(RATE)))
    m.stereo = stereo(x, pol)
    m.dynamics = dynamics(x, m.loudness)
    if m.stereo and m.stereo["mono_like"] and request and _asks_wide(request):
        m.findings.append(Finding("asked_wide_measured_mono", "intent_target",
                                  f"asked for wide, measured mono (correlation {m.stereo['correlation']:.2f}, "
                                  f"channels within {abs(m.stereo['level_diff_db']):.1f} dB)",
                                  f"your request: {request.strip()!r}", m.stereo["correlation"],
                                  float(_rule(pol, "mono_like")["number"])))
    metrics = ["validity", "bands", "centroid"] + (["loudness"] if m.loudness else []) \
        + (["stereo"] if m.stereo else []) + ["dynamics"]
    m.coverage = {"metrics": metrics, "request": bool(request), "scenes": []}
    if m.loudness is None:
        m.missing_facts.append("loudness (rate is not 48 kHz)")
    if m.stereo is None:
        m.missing_facts.append("stereo (the capture is mono)")
    if not request:
        m.missing_facts.append("the request (no intent to judge width against)")
    m.missing_facts.append("a reference to compare bands against (band numbers are evidence only)")
    intent = [f for f in m.findings if f.cls == "intent_target"]
    m.status = "concern" if intent else "verified"
    return m


_WIDE_WORDS = ("wide", "wider", "big", "bigger", "huge", "stereo", "lush", "spread")


def _asks_wide(request: str) -> bool:
    words = set(w.strip(".,!?").lower() for w in request.split())
    return any(w in words for w in _WIDE_WORDS)


def compare(a: Measurement, b: Measurement, baseline_label: str) -> dict:
    """Band deltas of a against b, b being the named baseline: 'x dB more
    body than <baseline>'. Loudness and width deltas alongside."""
    if not (a.valid and b.valid):
        raise MeasureError("compare needs two valid measurements")
    deltas = {k: round(a.bands[k] - b.bands[k], 2) for k in a.bands if k in b.bands}
    lines = []
    for k, d in deltas.items():
        if abs(d) >= 0.5:
            lines.append(f"{abs(d):.1f} dB {'more' if d > 0 else 'less'} {k.replace('_', ' ')} than {baseline_label}")
    la, lb = (a.loudness or {}).get("integrated_lufs"), (b.loudness or {}).get("integrated_lufs")
    loud_delta = round(la - lb, 2) if la is not None and lb is not None else None
    if loud_delta is not None and abs(loud_delta) >= 0.5:
        lines.append(f"{abs(loud_delta):.1f} LU {'louder' if loud_delta > 0 else 'quieter'} than {baseline_label}")
    width = None
    if a.stereo and b.stereo:
        width = round(a.stereo["correlation"] - b.stereo["correlation"], 4)
    return {"baseline": baseline_label, "band_deltas_db": deltas, "loudness_delta_lu": loud_delta,
            "correlation_delta": width, "lines": lines or [f"no difference of 0.5 dB or more from {baseline_label}"]}


# --- scene balance ----------------------------------------------------------------------------------------

ROLES = ("clean", "rhythm", "lead", "other")


def scene_balance(items: list[dict], pol: dict | None = None) -> dict:
    """items: [{scene: int, role: clean|rhythm|lead|other, lufs: float}].
    The enforced intent_target rules, each finding naming its baseline:
    the rhythm scenes' median. No rhythm scene: nothing to judge against,
    and that is said rather than assumed."""
    pol = pol or policy()
    rows = [dict(i) for i in items if i.get("lufs") is not None]
    rhythm = [r for r in rows if r.get("role") == "rhythm"]
    findings: list[Finding] = []
    coverage = {"scenes": [r["scene"] for r in rows], "metrics": ["integrated_lufs"],
                "roles": sorted({str(r.get("role")) for r in rows})}
    if not rhythm:
        return {"status": "unknown", "baseline": None, "findings": [], "coverage": coverage,
                "evidence": {"captures": len(rows)},
                "missing_facts": ["a rhythm scene: the balance rules are relative to the rhythm scenes"],
                "note": "no rhythm scene measured; nothing to judge the others against"}
    ref = float(np.median([r["lufs"] for r in rhythm]))
    base = f"rhythm median {ref:.1f} LUFS (scene{'s' if len(rhythm) > 1 else ''} " \
           f"{', '.join(str(r['scene']) for r in rhythm)})"
    r1 = _rule(pol, "rhythm_within_1lu")
    for r in rhythm:
        d = r["lufs"] - ref
        if abs(d) > float(r1["number"]) and len(rhythm) > 1:
            findings.append(Finding(r1["id"], r1["cls"], f"scene {r['scene']} (rhythm) is {abs(d):.1f} LU "
                                    f"{'above' if d > 0 else 'below'} the other rhythm scenes", base, round(d, 2),
                                    float(r1["number"])))
    r2, r3, r4 = _rule(pol, "lead_2_to_3lu_above_rhythm"), _rule(pol, "cap_4lu_above_rhythm"), \
        _rule(pol, "clean_not_below_rhythm")
    for r in rows:
        d = r["lufs"] - ref
        if d > float(r3["number"]):
            findings.append(Finding(r3["id"], r3["cls"], f"scene {r['scene']} ({r.get('role')}) is {d:.1f} LU "
                                    f"above the rhythm scenes; the cap is {r3['number']:g} LU", base, round(d, 2),
                                    float(r3["number"])))
            continue
        if r.get("role") == "lead" and not float(r2["number"]) <= d <= float(r2["number_max"]):
            findings.append(Finding(r2["id"], r2["cls"], f"scene {r['scene']} (lead) is {d:+.1f} LU against the "
                                    f"rhythm scenes; leads sit {r2['number']:g} to {r2['number_max']:g} LU above",
                                    base, round(d, 2), float(r2["number"])))
        if r.get("role") == "clean" and d < float(r4["number"]):
            findings.append(Finding(r4["id"], r4["cls"], f"scene {r['scene']} (clean) is {abs(d):.1f} LU below "
                                    "the rhythm scenes", base, round(d, 2), float(r4["number"])))
    missing = []
    skipped = [i.get("scene") for i in items if i.get("lufs") is None]
    if skipped:
        missing.append(f"loudness for scene{'s' if len(skipped) > 1 else ''} {', '.join(map(str, skipped))}")
    if len(rhythm) == 1:
        missing.append("a second rhythm scene (rhythm_within_1lu needs two)")
    return {"status": "concern" if findings else "verified", "baseline": base,
            "rhythm_median_lufs": round(ref, 2), "coverage": coverage,
            "evidence": {"captures": len(rows), "repeatability": "one capture per scene"},
            "missing_facts": missing,
            "scenes": [{"scene": r["scene"], "role": r.get("role"), "lufs": round(r["lufs"], 2),
                        "delta_lu": round(r["lufs"] - ref, 2)} for r in rows],
            "findings": [f.as_dict() for f in findings]}
