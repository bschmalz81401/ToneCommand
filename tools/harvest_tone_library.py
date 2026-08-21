#!/usr/bin/env python3
"""Harvest a private tone-reference library from the presets on the device.

Moncy's FM9 carries purchased, professionally dialed presets (AustinBuddy,
Boutique Tones, Gift of Tone, Worship Tutorials). Their voicings - amp
settings, cab choices, block chains, drive pairings - are the reference
points new builds should start from instead of factory defaults.

READ-ONLY by construction: preset selects and reads only. No writes, no
stores, ever. Selecting presets does discard the edit buffer, so do not
run this with unsaved edits on the unit.

PRIVACY: output goes to kb/tone_library/ which is gitignored. These are
paid products; their settings are for local reference only and must never
be committed to the public repo.

Usage:
    python tools/harvest_tone_library.py [start] [end]     # default 0 511
    TONECOMMAND_SIM=1 python tools/harvest_tone_library.py 0 3   # dry run

Resumable: already-harvested preset numbers are skipped.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9.registry import Registry  # noqa: E402

OUT = ROOT / "kb" / "tone_library" / "presets.jsonl"
BLOCKS = ["DISTORT", "FUZZ", "CABINET", "COMP", "DELAY",
          "MULTITAP", "CHORUS", "REVERB"]

def main(start: int, end: int) -> None:
    reg = Registry()
    if os.environ.get("TONECOMMAND_SIM") == "1":
        from fm9.sim import SimFM9
        dev = SimFM9(reg)
    else:
        from fm9.device import FM9
        dev = FM9(reg)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if OUT.exists():
        with OUT.open() as f:
            done = {json.loads(line)["number"] for line in f if line.strip()}
    todo = [n for n in range(start, end + 1) if n not in done]
    print(f"harvesting {len(todo)} presets ({len(done)} already done)")
    with OUT.open("a") as f:
        for i, n in enumerate(todo):
            got = dev.select_preset(n)
            if got is None:
                print(f"{n}: no answer, skipping"); continue
            time.sleep(0.15)
            blocks = dev.status_dump() or []
            rec = {"number": n, "name": got[1], "blocks": [], "harvested": True}
            present = {}
            for b in blocks:
                fam = reg.family_of_effect_id(b.effect_id)
                if fam:
                    present.setdefault(fam[0], b)
            for fam in BLOCKS:
                if fam not in present:
                    continue
                b = present[fam]
                eid = b.effect_id
                vals = dev.bulk_read(eid)
                if not vals:
                    continue
                chans = max(1, dev._channels.get(eid, 1))
                stride = len(vals) // chans if chans > 1 else len(vals)
                rec["blocks"].append({
                    "family": fam, "effect_id": eid,
                    "channel": b.channel, "bypassed": b.bypassed,
                    "channels": chans,
                    "params_wire": vals[:stride * chans],
                })
            f.write(json.dumps(rec) + "\n")
            f.flush()
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(todo)} ({n}: {got[1]!r})")
    print(f"done -> {OUT}")

if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[0]) if a else 0, int(a[1]) if len(a) > 1 else 511)
