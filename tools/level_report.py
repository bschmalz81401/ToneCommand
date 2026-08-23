#!/usr/bin/env python3
"""Per-scene level report for a preset range. Read-only.

    python tools/level_report.py 141 154
    TONECOMMAND_SIM=1 python tools/level_report.py 0 1

For every named scene, prints the level-relevant parameters on the
ACTIVE channel of each level-bearing block: amp level (dB), volume
block gain (0-10 scale), and the level of any engaged drive (0-10
scale). Values on different scales are reported side by side, never
summed into a fake loudness number; perceived loudness is judged by
ear, this report only makes outliers visible on paper.

Flags, per the leveling convention in kb/HARDWARE_RULES.md:
- amp level more than 3 dB ABOVE the preset's reference scene
  (scene 3 if present, else the preset's median) = sudden-loudness risk
- amp level spread across a preset's scenes wider than 6 dB = the
  volumes-all-over-the-place signature

Writes kb/tone_library/level_report.json.
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

HOT_DB = 3.0
SPREAD_DB = 6.0


def main(a: int, b: int) -> int:
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    amp_lvl = reg.find_param("DISTORT", "Level")
    vol_gain = reg.spec("VOLUME", 0)
    rows, flags = [], []
    with dev:
        for n in range(a, b + 1):
            dev.select_preset(n); time.sleep(0.3)
            preset_rows = []
            for sc in range(1, 9):
                dev.set_scene(sc); time.sleep(0.25)
                _, nm = dev.scene_name(sc)
                if nm.strip() in ("-", ""):
                    continue
                st = {x.effect_id: x for x in dev.status_dump() or []}

                def disp(spec, eid):
                    bk = st.get(eid)
                    if bk is None or bk.bypassed:
                        return None
                    w = dev.get_param_wire(spec, channel=bk.channel)
                    if w is None or spec.dmin is None:
                        return None
                    from fm9 import protocol as p
                    return round(p.normalized_to_display(
                        w / 65534, spec.dmin, spec.dmax, spec.scale), 2)

                drives = {}
                for inst in (1, 2):
                    try:
                        e = reg.effect_id("FUZZ", inst)
                    except Exception:
                        continue
                    v = disp(reg.spec("FUZZ", reg.find_param("FUZZ", "Level").param_id, inst), e)
                    if v is not None:
                        drives[f"drive{inst}"] = v
                row = {"preset": n, "scene": sc, "name": nm,
                       "amp_level_db": disp(amp_lvl, reg.effect_id("DISTORT")),
                       "vol_gain": disp(vol_gain, reg.effect_id("VOLUME")),
                       **drives}
                preset_rows.append(row)
                dr = " ".join(f"{k}={v}" for k, v in drives.items())
                print(f"{n} {sc}:{nm[:18]:18s} amp={row['amp_level_db']} "
                      f"vol={row['vol_gain']} {dr}")
            levels = {r["scene"]: r["amp_level_db"] for r in preset_rows
                      if r["amp_level_db"] is not None}
            if levels:
                ref = levels.get(3, statistics.median(levels.values()))
                ref_row = next((r for r in preset_rows if r["scene"] == 3), None)
                ref_vol = ref_row.get("vol_gain") if ref_row else None
                for r in preset_rows:
                    lv = r["amp_level_db"]
                    if lv is not None and lv - ref > HOT_DB:
                        r["flag"] = f"+{round(lv - ref, 1)} dB over reference"
                        flags.append(r)
                        print(f"  ^ FLAG {n} scene {r['scene']}: {r['flag']}")
                    # a plus/lead scene must never sit BELOW the reference on
                    # BOTH levers (amp level and trim): "more" cannot be quieter
                    boosty = any(k in r["name"].upper() for k in ("+", "LEAD"))
                    below_amp = lv is not None and lv < ref - 1.0
                    below_vol = (ref_vol is not None and r.get("vol_gain") is not None
                                 and r["vol_gain"] <= ref_vol)
                    if boosty and r["scene"] != 3 and below_amp and below_vol:
                        r["flag"] = (f"boost-named scene sits {round(ref - lv, 1)} dB "
                                     "under reference with no trim lift")
                        flags.append(r)
                        print(f"  ^ FLAG {n} scene {r['scene']}: {r['flag']}")
                spread = max(levels.values()) - min(levels.values())
                if spread > SPREAD_DB:
                    f = {"preset": n, "flag": f"amp level spread {round(spread, 1)} dB"}
                    flags.append(f)
                    print(f"  ^ FLAG {n}: {f['flag']}")
            rows.extend(preset_rows)
    out = ROOT / "kb" / "tone_library" / "level_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"rows": rows, "flags": flags}, out.open("w"), indent=1)
    print(f"\n{len(flags)} flags -> {out}")
    return 0 if not flags else 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), int(sys.argv[2])))
