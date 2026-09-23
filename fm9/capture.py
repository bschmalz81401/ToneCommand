"""Record the FM9's output for analysis (issue #100, G1).

Three capture methods, one per kind of measurement:

- 'test'    a 1 kHz tone at -18 dBFS plus a 20 Hz to 20 kHz log sweep, 4 s,
            replayed through the unit: spectrum and level;
- 'playing' a moment of the player's own playing, 6 s, prompted: scene
            balance and dynamics;
- 'silence' 4 s of nothing: the noise floor.

Every capture is a 48 kHz stereo wav from inputs 1/2 with a sidecar json
naming the method, channels, sample rate, the unit's preset and scene at
the time, and the routing state, so a measurement can always say what it
measured. Uses only the standard library's wave for the file.
"""
from __future__ import annotations

import json
import math
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

from . import reamp

METHODS = {
    "test": {"seconds": 4.0, "prompt": None,
             "what": "1 kHz tone at -18 dBFS then a 20 Hz to 20 kHz log sweep"},
    "playing": {"seconds": 6.0, "prompt": "play for six seconds, the way you would on stage",
                "what": "the player's own playing"},
    "silence": {"seconds": 4.0, "prompt": "do not play; the guitar volume down",
                "what": "silence, for the noise floor"},
}


def capture_method(kind: str) -> dict:
    """What a capture of `kind` consists of, or ValueError."""
    if kind not in METHODS:
        raise ValueError(f"no capture method {kind!r}; one of {sorted(METHODS)}")
    m = dict(METHODS[kind])
    m.update({"kind": kind, "rate": reamp.RATE, "channels": (1, 2)})
    m["signal"] = test_signal(m["seconds"]) if kind == "test" else None
    return m


def test_signal(seconds: float = 4.0, rate: int = reamp.RATE) -> np.ndarray:
    """1 kHz at -18 dBFS for the first half, a log sweep 20 Hz to 20 kHz for
    the second, 10 ms fades at the joins."""
    n = int(seconds * rate)
    half = n // 2
    t1 = np.arange(half) / rate
    tone = (10 ** (-18 / 20)) * np.sin(2 * math.pi * 1000 * t1)
    t2 = np.arange(n - half) / rate
    dur = (n - half) / rate
    f0, f1 = 20.0, 20000.0
    k = math.log(f1 / f0)
    phase = 2 * math.pi * f0 * dur / k * (np.exp(t2 * k / dur) - 1)
    sweep = (10 ** (-18 / 20)) * np.sin(phase)
    sig = np.concatenate([tone, sweep]).astype(np.float32)
    fade = int(0.01 * rate)
    ramp = np.linspace(0, 1, fade, dtype=np.float32)
    sig[:fade] *= ramp
    sig[-fade:] *= ramp[::-1]
    sig[half - fade:half] *= ramp[::-1]
    sig[half:half + fade] *= ramp
    return sig


def write_wav(path: Path, data: np.ndarray, rate: int = reamp.RATE) -> None:
    """16-bit PCM, channels as columns."""
    x = np.asarray(data, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    pcm = np.clip(x, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def record(kind: str, directory: Path, *, recorder, preset: Any = None,
           scene: Any = None, routing: Any = None, out_channel: int = 5) -> dict:
    """Capture `kind` into <directory>/<kind>-<time>.wav plus a sidecar.

    `recorder(signal_or_None, seconds, out_channel)` returns the recording
    (frames x 2); it is reamp.replay_and_record on hardware and a fake in
    tests. The 'test' kind replays the test signal; the other two record
    only. Returns the sidecar dict (with the wav path)."""
    m = capture_method(kind)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    # the scene in the name, and never overwrite: a per-scene loop (#102)
    # records several captures within one second
    base = f"{kind}-{stamp}" + (f"-s{int(scene)}" if scene not in (None, "") else "")
    wav = directory / f"{base}.wav"
    n = 2
    while wav.exists() or wav.with_suffix(".json").exists():
        wav = directory / f"{base}-{n}.wav"
        n += 1
    rec = recorder(m["signal"], m["seconds"], out_channel)
    rec = np.asarray(rec, dtype=np.float32)
    if rec.ndim != 2 or rec.shape[1] != 2:
        raise reamp.ReampError("the recording is not two channels (inputs 1/2)")
    write_wav(wav, rec)
    side = {"kind": kind, "what": m["what"], "seconds": m["seconds"],
            "rate": m["rate"], "channels": [1, 2], "frames": int(rec.shape[0]),
            "peak_dbfs": reamp.peak_dbfs(rec), "rms_dbfs": reamp.dbfs(rec),
            "out_channel": out_channel if kind == "test" else None,
            "preset": preset, "scene": scene, "routing": routing,
            "recorded_at": stamp, "wav": wav.name}
    wav.with_suffix(".json").write_text(json.dumps(side, indent=1), encoding="utf-8")
    side["path"] = str(wav)
    return side
