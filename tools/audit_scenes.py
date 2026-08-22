#!/usr/bin/env python3
"""Per-scene consistency audit of a preset range. Read-only.

    python tools/audit_scenes.py 141 148
    TONECOMMAND_SIM=1 python tools/audit_scenes.py 0 1

For every named scene: amp gain/level, delay/reverb mix on the ACTIVE
channel, engaged time-based effects. Flags scenes whose names promise
ambience (clean/ambient/swell) but run with no time-based effect - the
staging bug class that silently dried out half the 2026-08 setlist.
Writes kb/tone_library/scene_audit.json.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

WET = {"DELAY", "REVERB", "MULTITAP", "FDBKRET"}
PROMISES_WET = ("ambient", "swell", "clean", "cln")
PROMISES_DRY = ("dry", "crunch")

def main(a: int, b: int) -> int:
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    amp_gain = reg.spec("DISTORT", 11)
    amp_lvl = reg.find_param("DISTORT", "Level")
    dly_mix = reg.spec("DELAY", 0)
    rev_mix = reg.spec("REVERB", 11)
    rows, flags = [], []
    with dev:
        for n in range(a, b + 1):
            dev.select_preset(n); time.sleep(0.3)
            for sc in range(1, 9):
                dev.set_scene(sc); time.sleep(0.25)
                _, nm = dev.scene_name(sc)
                if nm.strip() in ("-", ""):
                    continue
                st = {x.effect_id: x for x in dev.status_dump() or []}
                def val(spec, fam):
                    bk = st.get(reg.effect_id(fam))
                    if bk is None or bk.bypassed:
                        return None
                    w = dev.get_param_wire(spec, channel=bk.channel)
                    return round(w / 65534 * 100, 1) if w is not None else None
                # both instances of every family: bypassing/checking only
                # instance 1 is how 154 scene 1 shipped wet (2026-08-22)
                on = []
                for f in ("FUZZ", *WET, "CHORUS", "PITCH"):
                    for inst in (1, 2):
                        try:
                            e = reg.effect_id(f, inst)
                        except Exception:
                            continue
                        bk = st.get(e)
                        if bk and not bk.bypassed:
                            on.append(f if inst == 1 else f + "2")
                row = {"preset": n, "scene": sc, "name": nm,
                       "amp_gain": val(amp_gain, "DISTORT"),
                       "amp_level": val(amp_lvl, "DISTORT"),
                       "delay_mix": val(dly_mix, "DELAY"),
                       "reverb_mix": val(rev_mix, "REVERB"), "active": on}
                rows.append(row)
                dry = not {x.removesuffix("2") for x in on} & WET
                promise = any(k in nm.lower() for k in PROMISES_WET)
                promise_dry = any(k in nm.lower() for k in PROMISES_DRY)
                mark = ""
                if dry and promise:
                    mark = "  <- DRY but name promises ambience"
                elif not dry and promise_dry:
                    mark = "  <- WET but name promises dry"
                print(f"{n} {sc}:{nm[:20]:20s} {','.join(on) or 'dry'}{mark}")
                if mark:
                    flags.append(row)
    out = ROOT / "kb" / "tone_library" / "scene_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"rows": rows, "flags": flags}, out.open("w"), indent=1)
    print(f"\n{len(flags)} flags -> {out}")
    return 0 if not flags else 1

if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), int(sys.argv[2])))
