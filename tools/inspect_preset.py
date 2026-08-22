#!/usr/bin/env python3
"""Read-only tone report for a preset: grid, per-scene states, key values.

    python tools/inspect_preset.py 144
    TONECOMMAND_SIM=1 python tools/inspect_preset.py 0

Selects the preset (discards any unsaved edit buffer; nothing else is
written) and prints what is actually there, so tweak sessions argue from
the device's truth instead of anyone's memory.
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

KEY_PARAMS = {
    "DISTORT": [("type", 10), ("gain", 11), ("master", 14), ("level", None)],
    "REVERB": [("type", 10), ("mix", 11)],
    "DELAY": [("mix", 12)],
    "MULTITAP": [("mix", 31)],
    "CHORUS": [("mix", 10)],
    "FUZZ": [("type", 0)],
}

def main(num: int) -> None:
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    with dev:
        got = dev.select_preset(num)
        print(f"== preset {got[0]}: {got[1]!r} ==")
        time.sleep(0.2)
        blocks = dev.status_dump() or []
        fams = {}
        for b in blocks:
            f = reg.family_of_effect_id(b.effect_id)
            if f:
                fams.setdefault(f[0], b)
        print("blocks:", ", ".join(sorted(fams)))
        for fam, b in sorted(fams.items()):
            if fam not in KEY_PARAMS:
                continue
            vals = dev.bulk_read(b.effect_id)
            if not vals:
                continue
            chans = max(1, dev._channels.get(b.effect_id, 1))
            stride = len(vals) // chans if chans > 1 else len(vals)
            base = min(b.channel, chans - 1) * stride
            bits = []
            for label, pid in KEY_PARAMS[fam]:
                if pid is None or base + pid >= len(vals):
                    continue
                w = vals[base + pid]
                if label == "type" and fam == "DISTORT":
                    bits.append(f"type={reg.amp_description(w)}")
                elif label == "type" and fam == "FUZZ":
                    bits.append(f"type={reg.drive_description(w)}")
                elif label == "type" and fam == "REVERB":
                    bits.append(f"type={reg.reverb_roster.get(str(w), w)}")
                else:
                    bits.append(f"{label}={round(w / 65534 * 100)}%")
            print(f"  {fam} ch{'ABCD'[b.channel]}: " + ", ".join(bits))
        from fm9 import protocol as p
        bpm = dev._request(p.build_get_tempo(),
                           lambda d: p.decode14(d[5], d[6])
                           if p.is_fractal(d, p.FN_TEMPO_BPM) and len(d) >= 7
                           else None)
        print(f"tempo: {bpm} BPM")
        for slot in range(1, 17):
            vals = dev.bulk_read(p.mod_slot_eid(slot))
            if not vals or not vals[p.MOD_PID_SOURCE]:
                continue
            tgt = reg.family_of_effect_id(vals[p.MOD_PID_TARGET_EFFECT])
            print(f"  modifier slot {slot}: source {vals[p.MOD_PID_SOURCE]} -> "
                  f"{tgt[0] if tgt else vals[p.MOD_PID_TARGET_EFFECT]} "
                  f"pid {vals[p.MOD_PID_TARGET_PARAM]}")
        cells = dev.read_grid() or []
        occ = sorted(cells, key=lambda c: (c.row, c.col))
        starved = [(c.row + 1, c.col + 1) for c in occ
                   if c.cable_in_mask == 0 and c.col > 0
                   and reg.family_of_effect_id(c.effect_id or 0)
                   and reg.family_of_effect_id(c.effect_id or 0)[0] != "INPUT"]
        if starved:
            print("CHAIN WARNING: cells with NO input cable (possible silent "
                  "break, or an undecoded mask corner):")
            for r, c in starved:
                print(f"  row {r} col {c}")
        print("scenes:")
        for sc in range(1, 9):
            dev.set_scene(sc)
            time.sleep(0.25)
            _, sname = dev.scene_name(sc)
            state = {b.effect_id: b for b in dev.status_dump() or []}
            on = []
            for fam, b0 in sorted(fams.items()):
                b = state.get(b0.effect_id)
                if b and not b.bypassed:
                    on.append(f"{fam}:{'ABCD'[b.channel]}")
            print(f"  {sc} {sname!r}: {' '.join(on)}")
        dev.set_scene(1)

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
