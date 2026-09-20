#!/usr/bin/env python3
"""The Gate 0 spike, one step at a time (issue #56), driven by the Pilot.

    tools/reamp_spike.py device          the FM9 as a USB audio device, channel map
    tools/reamp_spike.py routing         read Input 1 Source and Digital Source
    tools/reamp_spike.py observe         re-read after the Pilot changed them on
                                         the panel, and record the values seen
    tools/reamp_spike.py replay          replay the test signal on output 5,
                                         record inputs 1/2, say what came back
    tools/reamp_spike.py restore-test    apply the observed re-amp routing under
                                         the context manager and put it back,
                                         once normally and once through a forced
                                         exception; read back both times
    tools/reamp_spike.py journal         show or restore an outstanding journal

Nothing here writes a global to a value that was not first observed on the
unit (`observe`); the front-panel changes are the Pilot's. The MIDI port is
this process's for the run (one owner: quit FM9-Edit and stop the server
first). Monitors down before `replay`: the tool caps its output at -12 dBFS
and cannot know what is downstream of the FM9.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fm9 import capture, reamp, routing  # noqa: E402
from fm9.device import FM9  # noqa: E402

OBSERVED_FILE = Path(__file__).resolve().parent.parent / "config" / "reamp_observed.json"


def _stamp() -> str:
    return time.strftime("%H:%M:%S")


def _load_observed():
    if OBSERVED_FILE.exists():
        routing.load_observed(json.loads(OBSERVED_FILE.read_text()))


def _save_observed():
    OBSERVED_FILE.parent.mkdir(parents=True, exist_ok=True)
    OBSERVED_FILE.write_text(json.dumps(
        {str(k): {str(o): d for o, d in v.items()} for k, v in routing.OBSERVED.items()},
        indent=1))


def cmd_device(_args):
    dev = reamp.find_device()
    print(f"{_stamp()} {dev.name}: index {dev.index}, {dev.rate:g} Hz, "
          f"{dev.inputs} in / {dev.outputs} out")
    import sounddevice as sd
    for i, d in enumerate(sd.query_devices()):
        mark = " <-" if i == dev.index else ""
        print(f"   [{i}] {d['name']}: {d['max_input_channels']} in / "
              f"{d['max_output_channels']} out @ {d['default_samplerate']:g}{mark}")


def cmd_routing(_args):
    fm9 = FM9()
    try:
        r = routing.read_routing(fm9)
        for pid, v in r.items():
            print(f"{_stamp()} {v['name']} (param {pid}): ordinal {v['ordinal']}, "
                  f"display {v['display']!r}")
        return r
    finally:
        fm9.close()


def cmd_observe(_args):
    _load_observed()
    r = cmd_routing(_args)
    for pid, v in r.items():
        routing.observe(pid, v["ordinal"], v["display"])
    _save_observed()
    print(f"{_stamp()} observed table now: {json.dumps(routing.OBSERVED)}")
    print(f"   saved to {OBSERVED_FILE}")


def cmd_replay(args):
    dev = reamp.find_device()
    sig = capture.test_signal(4.0)
    print(f"{_stamp()} replaying the test signal on output {args.out} of {dev.name}, "
          f"recording inputs 1/2, peak capped at {reamp.PEAK_DBFS} dBFS")
    rec = reamp.replay_and_record(sig, args.out, (1, 2), seconds=4.0, device=dev)
    verdict = reamp.distinguish(rec, sig)
    print(f"{_stamp()} came back: {verdict}; recorded RMS {reamp.dbfs(rec):.1f} dBFS, "
          f"peak {reamp.peak_dbfs(rec):.1f} dBFS")
    out = Path(args.out_dir) if args.out_dir else Path.home() / ".tonecommand" / "captures"
    out.mkdir(parents=True, exist_ok=True)
    wav = out / f"spike-{time.strftime('%Y%m%d-%H%M%S')}.wav"
    capture.write_wav(wav, rec)
    print(f"   saved {wav}")


def cmd_restore_test(args):
    _load_observed()
    fm9 = FM9()
    try:
        before = routing.read_routing(fm9)
        changes = {routing.IN1_SOURCE: args.in1, routing.DIGITAL_SOURCE: args.digital}
        print(f"{_stamp()} before: {json.dumps(before)}")
        with routing.temporary(fm9, changes):
            mid = routing.read_routing(fm9)
            print(f"{_stamp()} applied: {json.dumps(mid)}")
        after = routing.read_routing(fm9)
        print(f"{_stamp()} restored (normal exit): {json.dumps(after)}")
        assert {k: v['ordinal'] for k, v in after.items()} == \
            {k: v['ordinal'] for k, v in before.items()}, "NOT restored"
        try:
            with routing.temporary(fm9, changes):
                raise RuntimeError("forced failure inside the change")
        except RuntimeError as e:
            print(f"{_stamp()} raised as intended: {e}")
        after2 = routing.read_routing(fm9)
        print(f"{_stamp()} restored (after exception): {json.dumps(after2)}")
        assert {k: v['ordinal'] for k, v in after2.items()} == \
            {k: v['ordinal'] for k, v in before.items()}, "NOT restored"
        print(f"{_stamp()} journal present: {routing.journal_path().exists()}")
    finally:
        fm9.close()


def cmd_journal(args):
    path = routing.journal_path()
    if not path.exists():
        print(f"{_stamp()} no outstanding routing journal at {path}")
        return
    print(path.read_text())
    if args.restore:
        fm9 = FM9()
        try:
            print(f"{_stamp()} restored: {json.dumps(routing.restore_outstanding(fm9))}")
        finally:
            fm9.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("device")
    sub.add_parser("routing")
    sub.add_parser("observe")
    r = sub.add_parser("replay"); r.add_argument("--out", type=int, default=5)
    r.add_argument("--out-dir", default="")
    t = sub.add_parser("restore-test")
    t.add_argument("--in1", type=int, required=True, help="observed ordinal for Input 1 Source = Digital")
    t.add_argument("--digital", type=int, required=True, help="observed ordinal for Digital Source = USB")
    j = sub.add_parser("journal"); j.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    {"device": cmd_device, "routing": cmd_routing, "observe": cmd_observe,
     "replay": cmd_replay, "restore-test": cmd_restore_test, "journal": cmd_journal}[args.cmd](args)


if __name__ == "__main__":
    main()
