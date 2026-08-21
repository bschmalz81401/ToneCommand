#!/usr/bin/env python3
"""Build FM9AI-ES5 Rig (slot 149): Moncy's analog ES-5 rig as 8 scenes.

Run with TONECOMMAND_SIM=1 first (regression shakeout), then on hardware.
Edit-buffer only; storing is a separate explicit step (--store).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fm9.registry import Registry
from fm9 import protocol as p

SIM = os.environ.get("TONECOMMAND_SIM") == "1"
reg = Registry()
if SIM:
    from fm9.sim import SimFM9
    dev = SimFM9(reg)
else:
    from fm9.device import FM9
    dev = FM9(reg)
dev.status_dump()

ROW = 2   # row 1 even-col cable draws are undecoded (protocol limitation)
CHAIN = [("INPUT", 1), ("COMP", 1), ("FUZZ", 1), ("DISTORT", 1),
         ("CHORUS", 1), ("MULTITAP", 1), ("DELAY", 1), ("CABINET", 1),
         ("OUTPUT", 1)]

def eid(fam, inst=1):
    return reg.effect_id(fam, inst)

def ordinal_of_drive(name):
    for o, n in reg.drive_roster.items():
        if n == name:
            return int(o)
    raise KeyError(name)

VARIANTS = {
    149: {"name": "FM9AI-ES5 Lstar",
          # A cleans: Texas Star Clean, B 80s: Brit 800 2204 High,
          # C lead: Solo 100 Lead, D crunch: Class-A 30W TB
          "amp_ch": {0: 179, 1: 14, 2: 36, 3: 8},
          # cab channels per scene: A=509 Lonestar cab, B=510 4x12, D=134 AC30
          "cab_ch": [0, 0, 0, 0, 0, 3, 1, 1]},
    150: {"name": "FM9AI-ES5 AC30",
          # A cleans: Class-A 30W TB, crunch swaps to the Lonestar dirty
          # channel (Texas Star Lead); 80s and lead scenes unchanged
          "amp_ch": {0: 8, 1: 14, 2: 36, 3: 71},
          "cab_ch": [3, 3, 3, 3, 3, 0, 1, 1]},
}
SLOT = int(sys.argv[1]) if len(sys.argv) > 1 else 149
AMP_CH = VARIANTS[SLOT]["amp_ch"]
DRIVE_CH = {0: "Esoteric RCB", 1: "Blues OD", 2: "Blues OD", 3: "Maxoff 808"}   # B: Moncy ear-picked Blues OD over Super OD as BD2 stand-in; C: Pantheon stand-in

def set_enum(fam, pid, ordv, inst=1):
    spec = reg.spec(fam, pid, inst)
    r = dev.set_param_ordinal(spec, ordv)
    return r

def set_disp(fam, needle, value, inst=1):
    spec = reg.find_param(fam, needle)
    if spec is None:
        print(f"  !! no param '{needle}' in {fam}; skipped")
        return None
    r = dev.set_param_display(spec, value)
    ok = getattr(r, "ok", True)
    print(f"  {fam}.{spec.name} = {value} -> {'ok' if ok else r.detail}")
    return r

def main():
    dev.select_preset(SLOT)
    num, name = dev.current_preset()
    print(f"building in edit buffer of {num} '{name}'")

    # 1. wipe grid
    for c in list(dev.read_grid() or []):
        dev.place_block(c.row + 1, c.col + 1, 0)
    print("grid wiped")

    # 2. place chain and cable it left to right
    for i, (fam, inst) in enumerate(CHAIN):
        dev.place_block(ROW, i + 1, eid(fam, inst))
    for i in range(len(CHAIN) - 1):
        dev.connect_cells(ROW, i + 1, ROW)
    grid = {(c.row + 1, c.col + 1): c for c in dev.read_grid() or []}
    missing = [(fam, i + 1) for i, (fam, _) in enumerate(CHAIN)
               if (ROW, i + 1) not in grid or grid[(ROW, i + 1)].effect_id != eid(fam)]
    uncabled = [i + 2 for i in range(len(CHAIN) - 1)
                if (ROW, i + 2) in grid and not grid[(ROW, i + 2)].cable_in_mask]
    print(f"chain placed; missing={missing} uncabled_cols={uncabled}")

    # 3. amp models per channel
    for ch, ord_ in AMP_CH.items():
        dev.set_channel(eid("DISTORT"), ch)
        set_enum("DISTORT", 10, ord_)
    dev.set_channel(eid("DISTORT"), 0)
    # 4. drive types per channel
    for ch, name_ in DRIVE_CH.items():
        dev.set_channel(eid("FUZZ"), ch)
        set_enum("FUZZ", 0, ordinal_of_drive(name_))
    dev.set_channel(eid("FUZZ"), 0)
    print("amp + drive channels set")

    # 5. aurora multitap (basetype 1 = Aurora), lead delay, subtle chorus
    set_enum("MULTITAP", 0, 1)                  # MULTITAP_BASETYPE: Aurora
    set_disp("DELAY", "Time", 380.0)
    set_disp("DELAY", "Feedback", 30.0)
    set_disp("DELAY", "Mix", 22.0)
    set_disp("CHORUS", "Rate", 0.5)
    set_disp("CHORUS", "Depth", 30.0)
    set_disp("CHORUS", "Mix", 35.0)

    # 5b. donor clones (hardware runs only): 509 Aurora, cabs, tempo
    import json, time
    donor_path = "/private/tmp/claude-501/-Users-moncyabraham-Projects/e0f6f1c0-b505-45a7-8307-2928968fb816/scratchpad/donors.json"
    if not SIM and os.path.exists(donor_path):
        donors = json.load(open(donor_path))

        def write_block(fam, wires, inst=1):
            e = eid(fam, inst)
            for pid, wire in enumerate(wires):
                kind = reg.ranges.get(fam, {}).get(str(pid), {}).get("kind")
                if kind == "enum":
                    dev._send(p.build_set_param_discrete(e, pid, int(wire)))
                else:
                    dev._send(p.build_set_param_continuous(e, pid, wire / 65534.0))
                time.sleep(0.02)

        def verify_block(fam, wires, label, tol=655):
            e = eid(fam, inst=1)
            got = dev.bulk_read(e) or []
            chans = max(1, dev._channels.get(e, 1))
            stride = len(got) // chans if chans > 1 else len(got)
            got = got[:stride]
            bad = sum(1 for a, b in zip(wires, got) if abs(a - b) > tol)
            print(f"  clone {label}: {len(wires)} params, mismatches={bad}")

        write_block("MULTITAP", donors["mt509"])
        verify_block("MULTITAP", donors["mt509"], "Aurora<-509")
        for ch, key in ((0, "cab509"), (1, "cab510"), (3, "cab134")):
            dev.set_channel(eid("CABINET"), ch)
            write_block("CABINET", donors[key])
        dev.set_channel(eid("CABINET"), 0)
        verify_block("CABINET", donors["cab509"], "cab A<-509")
        # Timmons voicing: 509's dialed amp + comp settings, not defaults
        if donors.get("amp509") and SLOT == 149:
            write_block("DISTORT", donors["amp509"])      # channel A is active
            verify_block("DISTORT", donors["amp509"], "amp A<-509")
        if donors.get("comp509"):
            write_block("COMP", donors["comp509"])
            verify_block("COMP", donors["comp509"], "comp<-509")
        # tame the SLO lead level (defaults come in hot)
        dev.set_channel(eid("DISTORT"), 2)
        lvl = reg.find_param("DISTORT", "Level")
        if lvl:
            dev.set_param_display(lvl, -6.0)
        dev.set_channel(eid("DISTORT"), 0)
        dev._send(p.build_set_tempo(int(donors.get("tempo509") or 120)))
        print("donor clones written (incl. 509 amp/comp voicing), tempo set")

    # 6. pedal 2 on both delay mixes (floor per house rule)
    mt_mix = reg.spec("MULTITAP", 31)
    dl_mix = reg.find_param("DELAY", "Mix")
    dev.bind_modifier(1, mt_mix.effect_id, mt_mix.param_id, 11, min_norm=0.25, max_norm=0.5)
    dev.bind_modifier(2, dl_mix.effect_id, dl_mix.param_id, 11, min_norm=0.25, max_norm=0.5)
    print("pedal 2 bound to aurora + lead delay mix")

    # 7. scenes: (name, amp_ch, drive_ch|None, comp, chorus, aurora, delay)
    SCENES = [
        ("CLEAN Boost",  0, 0, True,  False, True,  False),
        ("CLEAN +Cho",   0, 0, True,  True,  True,  False),
        ("BD2",          0, 1, False, False, True,  False),
        ("BD2 +Comp",    0, 1, True,  False, True,  False),
        ("BD2 Cmp+Cho",  0, 1, True,  True,  True,  False),
        ("CRUNCH",       3, None, False, False, False, False),
        ("80s RHYTHM",   1, 2, False, False, False, False),
        ("LEAD SLO",     2, 3, False, False, False, True),
    ]
    cab_ch_by_scene = VARIANTS[SLOT]["cab_ch"]
    for i, (sname, amp_ch, drv_ch, comp, cho, aur, dly) in enumerate(SCENES, 1):
        dev.set_scene(i)
        dev.set_channel(eid("DISTORT"), amp_ch)
        dev.set_channel(eid("CABINET"), cab_ch_by_scene[i - 1])
        if drv_ch is None:
            dev.set_bypass(eid("FUZZ"), True)
        else:
            dev.set_channel(eid("FUZZ"), drv_ch)
            dev.set_bypass(eid("FUZZ"), False)
        dev.set_bypass(eid("COMP"), not comp)
        dev.set_bypass(eid("CHORUS"), not cho)
        dev.set_bypass(eid("MULTITAP"), not aur)
        dev.set_bypass(eid("DELAY"), not dly)
        dev.rename_scene(i, sname)
    dev.set_scene(1)
    dev.rename_preset(VARIANTS[SLOT]["name"])
    print("scenes programmed")

    # 8. verify pass
    fails = []
    for i, (sname, amp_ch, drv_ch, comp, cho, aur, dly) in enumerate(SCENES, 1):
        dev.set_scene(i)
        checks = [("COMP", not comp), ("CHORUS", not cho),
                  ("MULTITAP", not aur), ("DELAY", not dly)]
        if drv_ch is None:
            checks.append(("FUZZ", True))
        for fam, want in checks:
            got = dev.get_bypass(eid(fam))
            if got != want:
                fails.append((i, fam, want, got))
    dev.set_scene(1)
    print("VERIFY:", "ALL SCENE STATES OK" if not fails else f"FAILS: {fails}")
    return 0 if not fails else 1

if __name__ == "__main__":
    sys.exit(main())
