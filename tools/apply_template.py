#!/usr/bin/env python3
"""Apply the standard 8-scene template to a preset, from a mapping file.

    python tools/apply_template.py <preset> <mapping.json>

The mapping encodes the per-preset judgment (which existing scene fills
each template slot); this tool does the mechanics identically every
time: capture all scene states, unbind Pedal 2 from wet mixes, voice
the four volume-trim channels (A=reference 8.0, B=lead 9.0, C=cleans
9.0, D=beds 7.0), write the target scenes, rename them, store, and
cold-verify.

Mapping format (all keys optional except "scenes"):
{
  "unbind_slots": [6, 7],          // modifier slots to set source=None
  "delay_mix": 25.0,               // fixed DELAY1 chA mix after unbind
  "scenes": {
    "1": {"from": 6,               // copy captured scene 6's states
          "name": "CLEAN Chorus",
          "vol": "C",              // trim channel A/B/C/D
          "tweaks": {"110": [true, 0]}   // eid: [bypassed, channel]
    },
    "3": {"name": "CRUNCH", "vol": "A"}  // no "from": rename+trim only
  }
}

Scene states are copied through the same wire reads the audits use;
input/output/send blocks are never touched. The feedback Return is
never bypassed by this tool regardless of mapping (severed-path rule).
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.device import FM9  # noqa: E402
from fm9.registry import Registry  # noqa: E402
from fm9 import protocol as p  # noqa: E402

SKIP_EIDS = (37, 42, 182, 200, 201)   # input, output, send: never touched
VOL_CH = {"A": 0, "B": 1, "C": 2, "D": 3}
TRIMS = {0: 8.0, 1: 9.0, 2: 9.0, 3: 7.0}


def main(preset: int, mapping_path: str) -> int:
    m = json.load(open(mapping_path))
    reg = Registry()
    d = FM9(reg)
    vol = reg.effect_id("VOLUME")
    ret = reg.effect_id("FDBKRET")
    with d:
        d.select_preset(preset); time.sleep(0.5)
        num, name = d.current_preset()
        assert num == preset, f"on {num}, wanted {preset}"
        print(f"== {preset}: {name}")
        cap = {}
        for sc in range(1, 9):
            d.set_scene(sc); time.sleep(0.3)
            cap[sc] = {b.effect_id: (b.bypassed, b.channel)
                       for b in d.status_dump() or {}}
        print("captured 8 scenes")
        if m.get("unbind_pedal2", True):
            for slot in range(1, 17):
                vals = d.bulk_read(p.mod_slot_eid(slot))
                if vals and vals[p.MOD_PID_SOURCE] == 11:   # Pedal 2
                    d._drain()
                    d._send(p.build_set_param_continuous(
                        p.mod_slot_eid(slot), p.MOD_PID_SOURCE, 0.0))
                    time.sleep(0.25)
                    got = d.bulk_read(p.mod_slot_eid(slot))
                    print(f"slot {slot} (was Pedal 2) source -> "
                          f"{got[p.MOD_PID_SOURCE]}")
        if "delay_mix" in m:
            d.set_scene(1); time.sleep(0.3)
            r = d.set_param_display(reg.spec("DELAY", 0), m["delay_mix"])
            print("delay mix:", r.ok)
        has_vol = m.get("has_volume", True)
        if has_vol:
            for ch, val in TRIMS.items():
                d.set_channel(vol, ch); time.sleep(0.25)
                d.set_param_display(reg.spec("VOLUME", 0), val)
            print("volume trims voiced")
        if "voice_clean_amp" in m:
            amp = reg.effect_id("DISTORT")
            d.set_scene(1); time.sleep(0.3)
            chA = {pid: d.get_param_wire(reg.spec("DISTORT", pid), channel=0)
                   for pid in (10, 1, 11, 12, 13, 14, 15, 26, 30)}
            d.set_channel(amp, 1); time.sleep(0.3)
            d.set_param_ordinal(reg.spec("DISTORT", 10), chA[10]); time.sleep(0.3)
            for pid in (1, 11, 12, 13, 14, 15, 26, 30):
                if chA[pid] is not None:
                    d._drain()
                    d._send(p.build_set_param_continuous(amp, pid, chA[pid] / 65534))
                    time.sleep(0.08)
            time.sleep(0.3)
            g = m["voice_clean_amp"].get("gain", 2.5)
            r = d.set_param_display(reg.spec("DISTORT", 11), g)
            d.set_channel(amp, 0); time.sleep(0.2)
            print(f"clean amp chB voiced (gain {g}):", r.ok)
        for sc_str, spec in m["scenes"].items():
            sc = int(sc_str)
            d.set_scene(sc); time.sleep(0.35)
            states = dict(cap[spec["from"]]) if "from" in spec else {}
            for eid_str, (byp, ch) in spec.get("tweaks", {}).items():
                states[int(eid_str)] = (byp, ch)
            for eid, (byp, ch) in states.items():
                if eid in SKIP_EIDS or eid == vol:
                    continue
                if eid == ret:
                    byp = False   # the Return is never bypassed
                d.set_channel(eid, ch); time.sleep(0.1)
                d.set_bypass(eid, byp); time.sleep(0.08)
            if has_vol:
                d.set_channel(vol, VOL_CH[spec["vol"]]); time.sleep(0.12)
                d.set_bypass(vol, False); time.sleep(0.08)
            d.rename_scene(sc, spec["name"]); time.sleep(0.25)
            print(f"scene {sc} {spec['name']}: set")
        d.set_scene(3); time.sleep(0.3)
        d.store_preset(preset); time.sleep(1.4)
        d.select_preset(133); time.sleep(0.4)
        d.select_preset(preset); time.sleep(0.5)
        _, nm = d.scene_name(3)
        ok = nm == m["scenes"].get("3", {}).get("name", nm)
        print(f"cold: scene 3 = {nm!r} -> {'OK' if ok else 'MISMATCH'}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), sys.argv[2]))
