"""The FM9 as a USB audio device: replay a DI through it and record the
processed return (issue #56, Gate 0).

Everything here is bounded so the spike cannot hurt anything downstream of
the FM9's outputs: the output peak is capped, a run is at most ten
seconds, only one run at a time, and the device lock the MIDI side holds
is taken for the whole run so audio never overlaps a MIDI write.
`distinguish` tells a processed return from a loopback (the DI came back
unprocessed) or silence, so a dry return is never reported as the unit's
output. Tests use a fake sounddevice; no real audio runs in CI.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass

import numpy as np

RATE = 48000
IN_CHANNELS = 8
OUT_CHANNELS = 8
MAX_SECONDS = 10.0
PEAK_DBFS = -12.0          # never louder than this at the FM9's USB input
SILENCE_DBFS = -60.0
LOOPBACK_CORR = 0.98

_run_lock = threading.Lock()


class ReampError(RuntimeError):
    """One line, written for the person at the rig."""


@dataclass
class Device:
    index: int
    name: str
    rate: float
    inputs: int
    outputs: int


def _sd():
    import sounddevice
    return sounddevice


def find_device(name_hint: str = "FM9", sd=None) -> Device:
    """The FM9's USB audio device, asserting 48 kHz and 8 in / 8 out. The
    channel numbers the OS reports are the ones this module uses (1-based
    in the API, as the FM9 manual and the DAW show them)."""
    sd = sd or _sd()
    hits = []
    for i, d in enumerate(sd.query_devices()):
        if name_hint.lower() in str(d.get("name", "")).lower():
            hits.append(Device(i, str(d["name"]), float(d.get("default_samplerate") or 0),
                               int(d.get("max_input_channels") or 0),
                               int(d.get("max_output_channels") or 0)))
    if not hits:
        raise ReampError(f"no USB audio device named like {name_hint!r}; is the "
                         "FM9's USB cable in and the unit on?")
    dev = hits[0]
    if int(round(dev.rate)) != RATE:
        raise ReampError(f"{dev.name} reports {dev.rate:g} Hz, not {RATE}; the "
                         "FM9 runs at 48 kHz, so something else answered")
    if dev.inputs < IN_CHANNELS or dev.outputs < OUT_CHANNELS:
        raise ReampError(f"{dev.name} reports {dev.inputs} in / {dev.outputs} out, "
                         f"not {IN_CHANNELS} / {OUT_CHANNELS}")
    return dev


def dbfs(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(x.astype(np.float64))))) if x.size else 0.0
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def peak_dbfs(x: np.ndarray) -> float:
    pk = float(np.max(np.abs(x))) if x.size else 0.0
    return 20 * math.log10(pk) if pk > 0 else -math.inf


def cap_peak(signal: np.ndarray, peak_db: float = PEAK_DBFS) -> np.ndarray:
    """The signal scaled DOWN so its peak is at most peak_db. Never up."""
    pk = float(np.max(np.abs(signal))) if signal.size else 0.0
    limit = 10 ** (peak_db / 20)
    if pk > limit:
        return (signal * (limit / pk)).astype(np.float32)
    return signal.astype(np.float32)


def replay_and_record(signal: np.ndarray, out_channel: int, in_channels=(1, 2),
                      seconds: float | None = None, device: Device | None = None,
                      sd=None, device_lock: threading.Lock | None = None) -> np.ndarray:
    """Play `signal` (mono, float, 48 kHz) on computer output `out_channel`
    (1-based) while recording `in_channels` (1-based), for min(len, cap).
    Returns the recording, shape (frames, len(in_channels)).

    Caps: peak at PEAK_DBFS, MAX_SECONDS, one run at a time, and the given
    device lock held throughout so no MIDI write overlaps.
    """
    sd = sd or _sd()
    device = device or find_device(sd=sd)
    seconds = MAX_SECONDS if seconds is None else min(float(seconds), MAX_SECONDS)
    n = min(int(seconds * RATE), int(len(signal)))
    if n <= 0:
        raise ReampError("nothing to play")
    if not 1 <= out_channel <= device.outputs:
        raise ReampError(f"output channel {out_channel} is off the device")
    for c in in_channels:
        if not 1 <= c <= device.inputs:
            raise ReampError(f"input channel {c} is off the device")
    mono = cap_peak(np.asarray(signal[:n], dtype=np.float32))
    out = np.zeros((n, device.outputs), dtype=np.float32)
    out[:, out_channel - 1] = mono
    if not _run_lock.acquire(blocking=False):
        raise ReampError("a replay is already running; one at a time")
    try:
        lock = device_lock or threading.Lock()
        with lock:
            rec = sd.playrec(out, samplerate=RATE, device=device.index,
                             channels=device.inputs, dtype="float32")
            sd.wait()
    finally:
        _run_lock.release()
    rec = np.asarray(rec, dtype=np.float32)
    idx = [c - 1 for c in in_channels]
    return rec[:, idx]


def _norm_xcorr_peak(a: np.ndarray, b: np.ndarray) -> float:
    """Peak of the normalised cross-correlation of two mono signals, over
    every lag, via FFT. 1.0 means b is a scaled, delayed copy of a."""
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    a = a - a.mean(); b = b - b.mean()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    n = 1 << (len(a) + len(b) - 1).bit_length()
    fa = np.fft.rfft(a, n); fb = np.fft.rfft(b, n)
    corr = np.fft.irfft(fa * np.conj(fb), n)
    return float(np.max(np.abs(corr)) / (na * nb))


def distinguish(recorded: np.ndarray, di: np.ndarray) -> str:
    """'silence' (below SILENCE_DBFS RMS), 'loopback' (the DI came back
    unprocessed: normalised cross-correlation above LOOPBACK_CORR at some
    lag), else 'processed'. A dry return is never called the unit's
    output."""
    rec = np.asarray(recorded, dtype=np.float64)
    mono = rec.mean(axis=1) if rec.ndim == 2 else rec
    if dbfs(mono) < SILENCE_DBFS:
        return "silence"
    di = np.asarray(di, dtype=np.float64)
    m = min(len(mono), len(di))
    if _norm_xcorr_peak(di[:m], mono[:m]) >= LOOPBACK_CORR:
        return "loopback"
    return "processed"
