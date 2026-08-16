#!/usr/bin/env python3
"""Build the FM9AI Van Halen Balance test preset in slot 133's edit buffer.

Scene 1: flanger. Scene 2: phaser. Scene 3: wah. Each effect on Pedal 2.
Pedal 1 (global volume) is never touched. Nothing is stored to flash.
"""
import time

from fm9.device import FM9
from fm9.registry import Registry
from fm9 import protocol as p

reg = Registry()
fm9 = FM9(reg)
results = []

def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} {detail}")
    results.append(ok)

print("== reset preset 133 ==")
print(fm9.select_preset(133))
time.sleep(0.5)
fm9.status_dump()

WAH, PHASER, FLANGER = 94, 90, 82

# Source ordinal 11 = Pedal 2 (EXP/SW TIP) on the FM3 enum; the FM9 enum is
# expected to match. Physically verified by rocking Pedal 2 (see verify_pedal.py).
pedal2 = 11

print("\n== rename preset + scenes ==")
fm9.rename_preset("FM9AI-VH Balance")
fm9.rename_scene(1, "FM9AI-Flanger")
fm9.rename_scene(2, "FM9AI-Phaser")
fm9.rename_scene(3, "FM9AI-Wah")
time.sleep(0.3)
pres = fm9.current_preset()
sc = fm9.scene_name(1)
check("preset renamed", pres is not None and pres[1].startswith("FM9AI-"), str(pres))
check("scene 1 renamed", sc is not None and sc[1].startswith("FM9AI-"), str(sc))

print("\n== place blocks on row-2 shunts ==")
fm9.place_block(2, 2, WAH)
fm9.place_block(2, 3, PHASER)
fm9.place_block(2, 4, FLANGER)
time.sleep(0.5)
cells = fm9.read_grid() or []
placed = {c.effect_id: (c.row + 1, c.col + 1, c.cable_in_mask) for c in cells
          if c.effect_id in (WAH, PHASER, FLANGER)}
check("wah at r2c2", placed.get(WAH, (0, 0, 0))[:2] == (2, 2), str(placed.get(WAH)))
check("phaser at r2c3", placed.get(PHASER, (0, 0, 0))[:2] == (2, 3), str(placed.get(PHASER)))
check("flanger at r2c4", placed.get(FLANGER, (0, 0, 0))[:2] == (2, 4), str(placed.get(FLANGER)))
check("cables intact", all(v[2] != 0 for v in placed.values()),
      f"masks={[bin(v[2]) for v in placed.values()]}")

print("\n== pin amp to channel A in scenes 1-3 ==")
for scene in (1, 2, 3):
    fm9.set_scene(scene)
    time.sleep(0.3)
    ch = fm9.set_channel(58, 0)
    check(f"scene {scene} amp channel A", ch == 0)
fm9.set_scene(1)
time.sleep(0.3)

print("\n== amp: PVH 6160 Block Lead + VH voicing ==")
amp_type = reg.spec("DISTORT", 10)
fm9.set_param_ordinal(amp_type, 39)
time.sleep(0.6)
w = fm9.get_param_wire(amp_type)
check("amp model", w == 39, f"= {reg.amp_roster.get(str(w), w)}")
for pid, val in ((11, 6.5), (12, 4.0), (13, 4.5), (14, 6.5), (30, 5.5), (26, 3.0), (15, 6.0)):
    spec = reg.spec("DISTORT", pid)
    r = fm9.set_param_display(spec, val)
    check(f"{spec.name} -> {val}", r.ok, r.detail)

print("\n== bind modifiers to Pedal 2 ==")
if pedal2 is not None:
    binds = [(1, WAH, 5, "wah control"), (2, PHASER, 2, "phaser rate"),
             (3, FLANGER, 1, "flanger rate")]
    for slot, eid, pid, label in binds:
        fm9.bind_modifier(slot, eid, pid, pedal2)
        vals = fm9.bulk_read(p.mod_slot_eid(slot))
        ok = (vals is not None and len(vals) > 9
              and vals[p.MOD_PID_TARGET_EFFECT] == eid
              and vals[p.MOD_PID_TARGET_PARAM] == pid
              and vals[p.MOD_PID_SOURCE] == pedal2)
        check(f"slot {slot}: Pedal 2 -> {label}", ok,
              f"(src={vals[0] if vals else '?'} eid={vals[8] if vals else '?'} pid={vals[9] if vals else '?'})")
else:
    check("modifier binding skipped", False, "no Pedal 2 ordinal found")

print("\n== scenes: 1=flanger 2=phaser 3=wah ==")
scene_fx = {1: FLANGER, 2: PHASER, 3: WAH}
for scene, active in scene_fx.items():
    got = fm9.set_scene(scene)
    time.sleep(0.3)
    for eid in (WAH, PHASER, FLANGER):
        fm9.set_bypass(eid, eid != active)
        time.sleep(0.1)
    states = {eid: fm9.get_bypass(eid) for eid in (WAH, PHASER, FLANGER)}
    ok = (got == scene and states[active] is False
          and all(states[e] for e in (WAH, PHASER, FLANGER) if e != active))
    check(f"scene {scene} bypass states", ok, str(states))

fm9.set_scene(1)
print("\n== store to test slot 133 ==")
stored = fm9.store_preset(133)
check("stored to 133", stored is not None and stored[0] == 133
      and stored[1].startswith("FM9AI-"), str(stored))

print("\n== final state ==")
print("preset:", fm9.current_preset())
print("scene:", fm9.scene_name())
fm9.close()
print(f"\n{sum(results)}/{len(results)} checks passed")
