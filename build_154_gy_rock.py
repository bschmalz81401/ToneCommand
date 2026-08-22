#!/usr/bin/env python3
"""154: Goodbye Yesterday rock cut. Clone of 144; scene 1 becomes a dry
crunch rock rhythm: amp chB = Brit 800 2204 High at moderate gain, cab
chB = the harvested 4x12, every effect bypassed. Scenes 2-8 unchanged.
Sim-test with TONECOMMAND_SIM=1 first."""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

DONORS = "/private/tmp/claude-501/-Users-moncyabraham-Projects/e0f6f1c0-b505-45a7-8307-2928968fb816/scratchpad/donors.json"

def main() -> int:
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    def eid(f, inst=1): return reg.effect_id(f, inst)
    with dev:
        dev.select_preset(144); time.sleep(0.4)
        dev.store_preset(154); time.sleep(1.0)
        dev.select_preset(154); time.sleep(0.4); dev.status_dump()
        dev.rename_preset("FM9AI-GYesterday Rock")
        # amp chB: Brit 800 2204 High, moderate gain (crunch, not metal)
        dev.set_channel(eid("DISTORT"), 1); time.sleep(0.3)
        dev.set_param_ordinal(reg.spec("DISTORT", 10), 14); time.sleep(0.3)
        g = reg.spec("DISTORT", 11)
        r = dev.set_param_display(g, 4.0)
        print("amp chB Brit 800, gain 4.0:", "ok" if r.ok else r.detail)
        # cab chB: the harvested 4x12 (hardware runs only)
        if os.path.exists(DONORS) and os.environ.get("TONECOMMAND_SIM") != "1":
            donors = json.load(open(DONORS))
            from fm9 import protocol as p
            dev.set_channel(eid("CABINET"), 1); time.sleep(0.3)
            for pid, wire in enumerate(donors["cab510"]):
                kind = reg.ranges.get("CABINET", {}).get(str(pid), {}).get("kind")
                if kind == "enum":
                    dev._send(p.build_set_param_discrete(eid("CABINET"), pid, int(wire)))
                else:
                    dev._send(p.build_set_param_continuous(eid("CABINET"), pid, wire / 65534.0))
                time.sleep(0.02)
            print("cab chB <- 510 4x12")
        dev.set_channel(eid("DISTORT"), 0); dev.set_channel(eid("CABINET"), 0)
        # scene 1: dry crunch, everything off
        dev.set_scene(1); time.sleep(0.3)
        dev.set_channel(eid("DISTORT"), 1)
        dev.set_channel(eid("CABINET"), 1)
        # both instances of every wet family. NEVER bypass FDBKRET here:
        # in this rig the main path crosses the send/return bus (input
        # chain ends at Send, Return heads the chain feeding the amps),
        # so a bypassed Return severs ALL signal (hardware-observed
        # 2026-08-22). The delays/reverbs are inline with Thru bypass,
        # so bypassing them is what makes the scene dry.
        wet_eids = [eid(f, i) for f in ("FUZZ", "DELAY", "REVERB", "COMP")
                    for i in (1, 2)]
        for e in wet_eids:
            dev.set_bypass(e, True)
            time.sleep(0.15)
        dev.rename_scene(1, "INTRO Crunch")
        dev.set_scene(1)
        dev.store_preset(154); time.sleep(1.2)
        dev.select_preset(133); dev.select_preset(154); time.sleep(0.4)
        # cold verify
        dev.set_scene(1); time.sleep(0.3)
        st = {b.effect_id: b for b in dev.status_dump() or []}
        amp = st.get(eid("DISTORT"))
        dry = all((st.get(e) is None or st[e].bypassed) for e in wet_eids)
        w = dev.get_param_wire(reg.spec("DISTORT", 10), channel=1)
        ok = amp is not None and amp.channel == 1 and dry and w == 14
        print(f"cold verify: amp ch{'ABCD'[amp.channel] if amp else '?'} "
              f"type_wire={w} dry={dry} -> {'PASS' if ok else 'FAIL'}")
        print("stored:", dev.current_preset())
        return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
