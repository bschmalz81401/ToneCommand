#!/usr/bin/env python3
"""Tone lock: regression testing for your sound.

    python tools/tone_lock.py lock 141 148      # capture verified baselines
    python tools/tone_lock.py check 141 148     # diff live state vs baseline

A lock captures every block's full parameter state (all channels), the
tempo, scene names, and per-scene bypass/channel states. `check` re-reads
the device and reports every drifted parameter by name: "who touched my
preset" answered to the wire level. Read-only in both modes; locks live
in kb/tone_locks/ (private).
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

LOCKS = ROOT / "kb" / "tone_locks"
TOL = 200          # wire16 tolerance for float params (~0.3%)

def capture(dev, reg, n):
    dev.select_preset(n); time.sleep(0.3)
    from fm9 import protocol as p
    blocks = dev.status_dump() or []
    rec = {"number": n, "name": dev.current_preset()[1], "blocks": {}, "scenes": {}}
    bpm = dev._request(p.build_get_tempo(),
                       lambda d: p.decode14(d[5], d[6])
                       if p.is_fractal(d, p.FN_TEMPO_BPM) and len(d) >= 7 else None)
    rec["tempo"] = bpm
    for b in blocks:
        fam = reg.family_of_effect_id(b.effect_id)
        if not fam:
            continue
        vals = dev.bulk_read(b.effect_id)
        if vals:
            rec["blocks"][str(b.effect_id)] = {"family": fam[0], "params": vals}
    for sc in range(1, 9):
        dev.set_scene(sc); time.sleep(0.25)
        st = dev.status_dump() or []
        rec["scenes"][str(sc)] = {
            "name": dev.scene_name(sc)[1],
            "state": {str(b.effect_id): [b.bypassed, b.channel] for b in st},
        }
    dev.set_scene(1)
    return rec

def diff(reg, old, new):
    out = []
    if old.get("tempo") != new.get("tempo"):
        out.append(f"tempo: {old.get('tempo')} -> {new.get('tempo')}")
    for eid, ob in old["blocks"].items():
        nb = new["blocks"].get(eid)
        if nb is None:
            out.append(f"{ob['family']}: block GONE"); continue
        op, np_ = ob["params"], nb["params"]
        if len(op) != len(np_):
            out.append(f"{ob['family']}: param count {len(op)} -> {len(np_)}")
            continue
        chans = max(1, len(op) // (len(op) or 1))
        for i, (a, b) in enumerate(zip(op, np_)):
            if abs(a - b) > TOL:
                pid = i % (len(op))   # stride-less fallback label
                pname = (reg.params.get((ob["family"], i)) or {}).get("name", f"pid{i}")
                out.append(f"{ob['family']}[{i}] {pname}: wire {a} -> {b}")
    for eid in new["blocks"]:
        if eid not in old["blocks"]:
            out.append(f"NEW block: {new['blocks'][eid]['family']}")
    for sc, os_ in old["scenes"].items():
        ns = new["scenes"].get(sc, {})
        if os_.get("name") != ns.get("name"):
            out.append(f"scene {sc} name: {os_.get('name')!r} -> {ns.get('name')!r}")
        for eid, (byp, ch) in os_.get("state", {}).items():
            nbyp_ch = ns.get("state", {}).get(eid)
            if nbyp_ch and (nbyp_ch[0] != byp or nbyp_ch[1] != ch):
                fam = old["blocks"].get(eid, {}).get("family", eid)
                out.append(f"scene {sc} {fam}: bypassed/channel "
                           f"{byp}/{ch} -> {nbyp_ch[0]}/{nbyp_ch[1]}")
    return out

def main(cmd, a, b):
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    LOCKS.mkdir(parents=True, exist_ok=True)
    drifted = False
    with dev:
        for n in range(a, b + 1):
            f = LOCKS / f"{n}.json"
            if cmd == "lock":
                rec = capture(dev, reg, n)
                f.write_text(json.dumps(rec))
                print(f"{n} {rec['name']!r}: LOCKED "
                      f"({len(rec['blocks'])} blocks, 8 scenes)")
            elif cmd == "check":
                if not f.exists():
                    print(f"{n}: no lock on file"); continue
                old = json.loads(f.read_text())
                new = capture(dev, reg, n)
                d = diff(reg, old, new)
                if d:
                    drifted = True
                    print(f"{n} {old['name']!r}: DRIFTED ({len(d)} changes)")
                    for line in d[:20]:
                        print(f"   {line}")
                else:
                    print(f"{n} {old['name']!r}: clean, matches the lock")
    return 1 if drifted else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
