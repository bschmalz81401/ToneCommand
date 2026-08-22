#!/usr/bin/env python3
"""Live 13-check hardware regression against the FM9.

Edit-buffer only. Every parameter is restored to its original value and
the run ends by re-selecting the stored preset, which discards the edit
buffer entirely. Nothing is ever stored on the unit.
"""
import time

from fm9.device import FM9
from fm9.registry import Registry

reg = Registry()
fm9 = FM9(reg)

print("== snapshot ==")
preset = fm9.current_preset()
scene = fm9.current_scene()
print(f"preset: {preset}, scene: {scene}")
assert preset is not None, "cannot read current preset; aborting"
preset_num = preset[0]

status = fm9.status_dump() or []
present = {}
for b in status:
    fam = reg.family_of_effect_id(b.effect_id)
    if fam:
        present[fam] = b
print("blocks:", {f"{fam[0]}{fam[1]}": f"byp={b.bypassed} ch={'ABCD'[b.channel]}"
                  for fam, b in present.items()})

results = []

def check(label, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label} {detail}")
    results.append((label, ok, detail))

# --- 1. GET sanity: read amp gain twice; a GET must not change the value ---
print("\n== amp gain GET ==")
drive = reg.spec("DISTORT", 11)   # DISTORT_DRIVE "Gain"
g1 = fm9.get_param_display(drive)
time.sleep(0.1)
g2 = fm9.get_param_display(drive)
print(f"gain reads: {g1} then {g2}")
check("GET amp gain returns a value", isinstance(g1, (int, float)))
check("GET is stable and non-destructive", g1 == g2, f"({g1} vs {g2})")

# --- 2. SET amp gain: nudge and restore ---
print("\n== amp gain SET/restore ==")
if isinstance(g1, (int, float)):
    target = round(g1 + (0.5 if g1 <= 9.0 else -0.5), 2)
    r = fm9.set_param_display(drive, target)
    check(f"SET amp gain {g1} -> {target}", r.ok, r.detail)
    rr = fm9.set_param_display(drive, g1)
    check(f"restore amp gain -> {g1}", rr.ok, rr.detail)

# --- 3. presence ---
print("\n== amp presence SET/restore ==")
pres = reg.spec("DISTORT", 30)    # DISTORT_PRESENCE
p1 = fm9.get_param_display(pres)
print(f"presence: {p1}")
if isinstance(p1, (int, float)):
    target = round(p1 + (0.5 if p1 <= 9.0 else -0.5), 2)
    r = fm9.set_param_display(pres, target)
    check(f"SET presence {p1} -> {target}", r.ok, r.detail)
    rr = fm9.set_param_display(pres, p1)
    check(f"restore presence -> {p1}", rr.ok, rr.detail)
else:
    check("presence read", False, str(p1))

# --- 4. gate threshold (Input 1 gate on this preset; GATE block if present) ---
print("\n== gate threshold SET/restore ==")
if ("GATE", 1) in present:
    gate = reg.spec("GATE", 0)
else:
    gate = reg.spec("INPUT", 0)   # INPUT_THRESH, Input 1 noise gate
t1 = fm9.get_param_display(gate)
print(f"{gate.name}: {t1}")
if isinstance(t1, (int, float)):
    target = round(min(0.0, t1 + 3.0), 2) if t1 < -3 else round(t1 - 3.0, 2)
    r = fm9.set_param_display(gate, target)
    check(f"SET {gate.name} {t1} -> {target}", r.ok, r.detail)
    rr = fm9.set_param_display(gate, t1)
    check(f"restore {gate.name} -> {t1}", rr.ok, rr.detail)
else:
    check("gate threshold read", False, str(t1))

# --- 5. scene select and back ---
print("\n== scene select ==")
orig_scene = scene or 1
other = 2 if orig_scene != 2 else 3
s = fm9.set_scene(other)
check(f"scene {orig_scene} -> {other}", s == other, f"device says {s}")
name = fm9.scene_name()
print(f"now on scene: {name}")
s = fm9.set_scene(orig_scene)
check(f"scene back -> {orig_scene}", s == orig_scene, f"device says {s}")

# --- 6. block bypass toggle (Delay 1, official 0x0A) ---
print("\n== bypass toggle ==")
delay_eid = reg.effect_id("DELAY", 1)
b1 = fm9.get_bypass(delay_eid)
print(f"delay1 bypassed: {b1}")
if b1 is not None:
    b2 = fm9.set_bypass(delay_eid, not b1)
    check(f"bypass {b1} -> {not b1}", b2 == (not b1), f"device says {b2}")
    b3 = fm9.set_bypass(delay_eid, b1)
    check(f"bypass restore -> {b1}", b3 == b1, f"device says {b3}")
else:
    check("bypass read", False, "no response")

# --- final: discard edit buffer by re-selecting the stored preset ---
print("\n== restore: re-select stored preset ==")
back = fm9.select_preset(preset_num)
check(f"re-selected preset {preset_num}", back is not None and back[0] == preset_num,
      str(back))

fm9.close()

print("\n== summary ==")
passed = sum(1 for _, ok, _ in results if ok)
print(f"{passed}/{len(results)} checks passed")
for label, ok, detail in results:
    if not ok:
        print(f"  FAILED: {label} {detail}")
