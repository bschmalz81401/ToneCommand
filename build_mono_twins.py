#!/usr/bin/env python3
"""Mono-ize the v2 presets: 151->154, 152->155, 153->156.

Follows the proven monoize recipe from the 145-148 build (PLAYBOOKS.md):
clone, then a post-everything MIXER in Mono mode (MIXER_MODE pid 14 = 1,
ear-confirmed 2026-08-20) feeding the output. Run at next power-on;
sim-test first. Stores only on full verification.
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent if (
    Path(__file__).resolve().parent.name == "tools") else Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

# 154 became the Goodbye Yesterday rock cut (2026-08-22); twins shifted
PAIRS = [(151, 155, "FM9AI-M-IKnowAName v2"),
         (152, 156, "FM9AI-M-WhoElse v2"),
         (153, 157, "FM9AI-M-WhatAGod v2")]

def main() -> int:
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    ok_all = True
    with dev:
        for src, dst, name in PAIRS:
            dev.select_preset(src); time.sleep(0.4)
            dev.store_preset(dst); time.sleep(1.0)
            dev.select_preset(dst); time.sleep(0.4)
            dev.status_dump()
            dev.rename_preset(name)
            # find the OUTPUT cell; put the mixer right before it
            cells = dev.read_grid() or []
            outs = [c for c in cells if c.effect_id == 42]   # Output 1 = main
            out = max(outs, key=lambda c: c.col) if outs else None
            if out is None:
                print(f"{dst}: no output cell found; SKIP"); ok_all = False; continue
            r, c = out.row + 1, out.col + 1
            prev = next((x for x in cells if (x.row + 1, x.col + 1) == (r, c - 1)), None)
            if prev is None or prev.effect_id is None or not prev.is_shunt:
                # need a free/shunt cell before output for the mixer
                if prev is not None and not prev.is_shunt:
                    print(f"{dst}: cell before output occupied by a block; "
                          f"needs a manual plan; SKIP")
                    ok_all = False
                    continue
            dev.place_block(r, c - 1, reg.effect_id("MIXER"))
            time.sleep(0.4)
            after = {(x.row + 1, x.col + 1): x for x in dev.read_grid() or []}
            mx = after.get((r, c - 1))
            if mx is None or mx.effect_id != reg.effect_id("MIXER"):
                print(f"{dst}: mixer failed to place (DSP budget?); SKIP")
                ok_all = False
                continue
            nxt = after.get((r, c))
            if nxt is not None and nxt.cable_in_mask == 0:
                dev.connect_cells(r, c - 1, r)
                time.sleep(0.4)
            dev.set_param_ordinal(reg.spec("MIXER", 14), 1)   # Mono, confirmed
            time.sleep(0.3)
            w = dev.get_param_wire(reg.spec("MIXER", 14))
            dev.store_preset(dst); time.sleep(1.2)
            dev.select_preset(133); dev.select_preset(dst); time.sleep(0.3)
            g = {(x.row + 1, x.col + 1): x for x in dev.read_grid() or []}
            good = (r, c - 1) in g and g[(r, c - 1)].effect_id == reg.effect_id("MIXER") \
                and g[(r, c)].cable_in_mask != 0 and w == 1
            print(f"{dst} {name!r}: {'MONO OK' if good else 'VERIFY FAILED'}")
            ok_all = ok_all and good
    return 0 if ok_all else 1

if __name__ == "__main__":
    sys.exit(main())
