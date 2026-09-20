"""Issues #56 (Gate 0) and #100 (G1): the re-amp path as code that can be
trusted before it meets the unit. A fake sounddevice stands in for the
FM9's USB audio; the sim stands in for the MIDI side. No real audio, no
port, no network.
"""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

import server
from fm9 import capture, reamp, routing
from fm9.sim import SimFM9


# --- a fake sounddevice --------------------------------------------------------

class FakeSD:
    """query_devices, playrec and wait, with a 'processing' function that
    turns what is played into what comes back on inputs 1/2."""

    def __init__(self, devices=None, process=None):
        self.devices = devices if devices is not None else [
            {"name": "MacBook Pro Speakers", "max_input_channels": 0,
             "max_output_channels": 2, "default_samplerate": 44100.0},
            {"name": "FM9", "max_input_channels": 8, "max_output_channels": 8,
             "default_samplerate": 48000.0},
        ]
        self.process = process or (lambda x: x)
        self.played = []

    def query_devices(self):
        return list(self.devices)

    def playrec(self, out, samplerate, device, channels, dtype):
        assert samplerate == 48000 and dtype == "float32"
        self.played.append((device, out.copy()))
        rec = np.zeros((len(out), channels), dtype=np.float32)
        src = out[:, 4] if out.shape[1] > 4 else out[:, 0]     # output 5
        y = self.process(src)
        rec[:, 0] = y
        rec[:, 1] = y
        self._rec = rec
        return rec

    def wait(self):
        return None


def _di(seconds=1.0, rate=48000):
    t = np.arange(int(seconds * rate)) / rate
    rng = np.random.default_rng(1)
    return (0.3 * np.sin(2 * np.pi * 220 * t) + 0.1 * rng.standard_normal(t.size)).astype(np.float32)


# --- device ---------------------------------------------------------------------

def test_device_is_found_by_name_at_48k_8_in_8_out():
    sd = FakeSD()
    dev = reamp.find_device(sd=sd)
    assert (dev.index, dev.name, dev.rate, dev.inputs, dev.outputs) == (1, "FM9", 48000.0, 8, 8)


def test_device_refusals_are_one_line_each():
    with pytest.raises(reamp.ReampError, match="no USB audio device named like"):
        reamp.find_device(sd=FakeSD(devices=[{"name": "Speakers", "max_input_channels": 0,
                                               "max_output_channels": 2,
                                               "default_samplerate": 48000.0}]))
    with pytest.raises(reamp.ReampError, match="not 48000"):
        reamp.find_device(sd=FakeSD(devices=[{"name": "FM9", "max_input_channels": 8,
                                               "max_output_channels": 8,
                                               "default_samplerate": 44100.0}]))
    with pytest.raises(reamp.ReampError, match="not 8 / 8"):
        reamp.find_device(sd=FakeSD(devices=[{"name": "FM9", "max_input_channels": 2,
                                               "max_output_channels": 2,
                                               "default_samplerate": 48000.0}]))


# --- channels and caps -------------------------------------------------------------

def test_replay_uses_the_named_channels_and_caps_the_peak():
    sd = FakeSD()
    loud = (_di() * 10).astype(np.float32)          # way over full scale
    rec = reamp.replay_and_record(loud, 5, (1, 2), seconds=0.5, sd=sd)
    device, out = sd.played[0]
    assert device == 1 and out.shape == (24000, 8)
    assert np.all(out[:, [0, 1, 2, 3, 5, 6, 7]] == 0)          # only output 5
    assert reamp.peak_dbfs(out[:, 4]) <= reamp.PEAK_DBFS + 1e-6
    assert rec.shape == (24000, 2)
    with pytest.raises(reamp.ReampError, match="off the device"):
        reamp.replay_and_record(loud, 9, (1, 2), seconds=0.5, sd=sd)
    with pytest.raises(reamp.ReampError, match="off the device"):
        reamp.replay_and_record(loud, 5, (1, 9), seconds=0.5, sd=sd)


def test_replay_never_exceeds_ten_seconds_and_holds_the_device_lock():
    sd = FakeSD()
    long = np.zeros(48000 * 30, dtype=np.float32) + 0.1
    lock = threading.Lock()
    held = []
    orig = sd.playrec

    def spy(*a, **k):
        held.append(lock.locked())
        return orig(*a, **k)
    sd.playrec = spy
    rec = reamp.replay_and_record(long, 5, (1, 2), seconds=99, sd=sd, device_lock=lock)
    assert rec.shape[0] == 48000 * 10 and held == [True] and not lock.locked()


# --- processed, loopback, silence ------------------------------------------------------

def test_distinguish_processed_loopback_and_silence():
    di = _di()
    # loopback: a delayed, scaled copy
    loop = np.concatenate([np.zeros(240, dtype=np.float32), di[:-240] * 0.5])
    assert reamp.distinguish(np.stack([loop, loop], 1), di) == "loopback"
    # processed: heavy nonlinearity plus filtering, no longer a copy
    proc = np.tanh(di * 12)
    proc = np.convolve(proc, np.ones(64) / 64, mode="same").astype(np.float32)
    assert reamp.distinguish(np.stack([proc, proc], 1), di) == "processed"
    # silence
    quiet = np.zeros_like(di) + 1e-5
    assert reamp.distinguish(np.stack([quiet, quiet], 1), di) == "silence"
    # the fake device end to end: an identity 'unit' is a loopback
    sd = FakeSD()
    rec = reamp.replay_and_record(di, 5, (1, 2), seconds=1.0, sd=sd)
    assert reamp.distinguish(rec, di) == "loopback"
    # a fake 'unit' that clips AND filters: tanh alone keeps a sine's shape
    # (correlation stays above the loopback line), which is right, a clipped
    # copy of the DI is still the DI; an amp also filters
    sd2 = FakeSD(process=lambda x: np.convolve(np.tanh(x * 12), np.ones(64) / 64,
                                                mode="same").astype(np.float32))
    rec2 = reamp.replay_and_record(di, 5, (1, 2), seconds=1.0, sd=sd2)
    assert reamp.distinguish(rec2, di) == "processed"


# --- routing --------------------------------------------------------------------------

@pytest.fixture
def sim(monkeypatch, tmp_path):
    monkeypatch.setenv("TONECOMMAND_ROUTING_JOURNAL", str(tmp_path / "journal.json"))
    routing.OBSERVED.clear()
    dev = SimFM9(server.reg)
    dev.status_dump()
    return dev


def _ordinals(fm9):
    r = routing.read_routing(fm9)
    return {k: v["ordinal"] for k, v in r.items()}


def test_routing_reads_both_globals_with_names(sim):
    r = routing.read_routing(sim)
    assert set(r) == {routing.IN1_SOURCE, routing.DIGITAL_SOURCE}
    assert r[routing.IN1_SOURCE]["name"] == "Input 1 Source"
    assert all(isinstance(v["ordinal"], int) for v in r.values())


def test_routing_refuses_an_unobserved_ordinal(sim):
    with pytest.raises(routing.RoutingError, match="never observed"):
        with routing.temporary(sim, {routing.IN1_SOURCE: 1}):
            pass
    assert not routing.journal_path().exists()


def test_routing_restore_on_success_and_on_exception(sim):
    before = _ordinals(sim)
    new1 = before[routing.IN1_SOURCE] + 1
    new2 = before[routing.DIGITAL_SOURCE] + 1
    routing.observe(routing.IN1_SOURCE, new1, "Digital")
    routing.observe(routing.DIGITAL_SOURCE, new2, "USB")
    with routing.temporary(sim, {routing.IN1_SOURCE: new1, routing.DIGITAL_SOURCE: new2}) as e:
        assert routing.journal_path().exists()
        mid = _ordinals(sim)
        assert mid == {routing.IN1_SOURCE: new1, routing.DIGITAL_SOURCE: new2}
        assert e[routing.IN1_SOURCE]["before"] == before[routing.IN1_SOURCE]
    assert _ordinals(sim) == before
    assert not routing.journal_path().exists()
    with pytest.raises(RuntimeError, match="forced"):
        with routing.temporary(sim, {routing.IN1_SOURCE: new1}):
            assert _ordinals(sim)[routing.IN1_SOURCE] == new1
            raise RuntimeError("forced")
    assert _ordinals(sim) == before
    assert not routing.journal_path().exists()


def test_routing_journal_restores_on_restart(sim):
    before = _ordinals(sim)
    new1 = before[routing.IN1_SOURCE] + 1
    routing.observe(routing.IN1_SOURCE, new1, "Digital")
    # the process dies mid-change: write the journal, apply, never restore
    routing._write_journal([{"param": routing.IN1_SOURCE,
                             "before": before[routing.IN1_SOURCE], "after": new1}])
    routing._write(sim, routing.IN1_SOURCE, new1)
    assert _ordinals(sim)[routing.IN1_SOURCE] == new1
    out = routing.restore_outstanding(sim)
    assert out["restored"] == [{"param": routing.IN1_SOURCE,
                                "ordinal": before[routing.IN1_SOURCE]}]
    assert _ordinals(sim) == before
    assert not routing.journal_path().exists()
    assert routing.restore_outstanding(sim) is None


# --- G1 captures -------------------------------------------------------------------

def test_capture_method_defines_the_three_kinds():
    t = capture.capture_method("test")
    assert t["seconds"] == 4.0 and t["rate"] == 48000 and t["channels"] == (1, 2)
    assert t["signal"].shape == (48000 * 4,)
    assert -19 < reamp.peak_dbfs(t["signal"][:48000]) < -17         # the tone at -18
    p = capture.capture_method("playing")
    assert p["seconds"] == 6.0 and p["prompt"] and p["signal"] is None
    s = capture.capture_method("silence")
    assert s["seconds"] == 4.0 and s["signal"] is None
    with pytest.raises(ValueError):
        capture.capture_method("loud")


def test_capture_record_writes_a_48k_stereo_wav_and_a_sidecar(tmp_path):
    import wave

    def recorder(signal, seconds, out_channel):
        n = int(seconds * 48000)
        y = (signal[:n] * 0.5) if signal is not None else np.zeros(n, dtype=np.float32)
        return np.stack([y, y], 1)
    side = capture.record("test", tmp_path, recorder=recorder, preset="P 139",
                          scene=1, routing={"in1": 2})
    wav = tmp_path / side["wav"]
    with wave.open(str(wav)) as w:
        assert (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (2, 48000, 2)
        assert w.getnframes() == 48000 * 4
    js = json.loads((tmp_path / side["wav"].replace(".wav", ".json")).read_text())
    assert js["kind"] == "test" and js["rate"] == 48000 and js["channels"] == [1, 2]
    assert js["preset"] == "P 139" and js["scene"] == 1 and js["routing"] == {"in1": 2}
    assert js["out_channel"] == 5 and js["frames"] == 48000 * 4
    quiet = capture.record("silence", tmp_path, recorder=recorder)
    assert quiet["out_channel"] is None and quiet["rms_dbfs"] == float("-inf")
