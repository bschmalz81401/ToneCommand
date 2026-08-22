#!/usr/bin/env python3
"""Generate config/effect_type_models.json: delay and chorus type facts.

Facts extracted from human-pasted copies of the Fractal wiki's "Delay
block" (edited 2026-06-29) and "Chorus block" (edited 2026-07-13) pages,
2026-08-21. Facts only; wiki prose is not reproduced.

NAME-KEYED, not ordinal-keyed: the FM9 catalog carries no delay/chorus
type rosters, and the enum ordering is undocumented. Never guess an
ordinal. Ordinal harvest is a hardware task (issue #5).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Fractal type name -> real-world reference, exactly as the wiki states it.
# None = the wiki names no real-world unit for this type (stays unmapped).
DELAY = {
    # wiki: "The model is matched to an original, not reissue, DMM";
    # section titled "Deluxe Memory Guy"
    "Deluxe Mind Guy": "Deluxe Memory Man (original, not reissue; 'DMM')",
    "Stereo Mind Guy": "Deluxe Memory Man (stereo variant of the same reference)",
    # wiki quote: "the DM-2 has a pre-emphasis/de-emphasis topology"
    "DM-Two Delay": "DM-2 analog delay",
    # wiki compander quote groups the three: "The DMM, DM2, and Carbon
    # Copy have it on by default"
    "Graphite Copy Delay": "Carbon Copy analog delay",
    # wiki quote: "The original 2290 was a 'bit slice' processor"; listed
    # among "rack effects processors from the 80s"
    "2290 W/ Modulation": "the 2290 (80s rack digital delay); NOTE: ships "
                          "with Phase Reverse = Right, silent if summed to "
                          "mono after the block",
    "Zephyr": "Fractal Audio original, not based on a real unit",
    "Vintage Digital": "early primitive 8-bit digital delay (era model, "
                       "no specific unit named)",
}
CHORUS = {
    # explicit "based on" statements on the wiki page
    "MX234": "MXR M234 Analog Chorus",
    # the types list says "MX234 Stereo"; the wiki section for it is
    # titled "MX134 Stereo" = MXR M134 Stereo Chorus (upstream naming
    # discrepancy preserved here rather than resolved)
    "MX234 Stereo": "MXR M134 Stereo Chorus (per wiki; section/list names "
                    "disagree upstream)",
    "Small Copy": "EHX Small Clone",
    "Japan CH-1": "Boss Super Chorus CH-1",
    "Rockguy": "Rockman X-100 chorus",
    "Japan CE-1 Chorus": "Boss CE-1 (pedal version of the Roland JC-120 "
                         "chorus), chorus mode",
    "Japan CE-1 Vibrato": "Boss CE-1, vibrato mode",
    "Japan CE-2": "Boss CE-2 (1979 mono chorus, successor of the CE-1)",
    "Japan CE-2 Bass": "Boss CE-2 Bass",
    "Dimension 1": "Roland SDD-320 Dimension D (types correspond to the "
                   "unit's modes)",
    "Dimension 2": "Roland SDD-320 Dimension D",
    "Dimension 3": "Roland SDD-320 Dimension D",
    "Stereo Tri-Chorus": "Dytronics Songbird TSC-1380",
}

# Fractal wiki "Multitap Delay block" page (edited 2026-06-27), human-
# pasted 2026-08-21. The "based on" statements are in the types list.
MULTITAP = {
    "1210": "TC 1210 Spatial Expander / Stereo Chorus Flanger",
    "A.H. Clean Long": "Allan Holdsworth's use of the Yamaha UD Stomp",
    "A.H. Clean Short": "Allan Holdsworth's use of the Yamaha UD Stomp",
    "A.H. Lead Long": "Allan Holdsworth's use of the Yamaha UD Stomp",
    "A.H. Lead Short": "Allan Holdsworth's use of the Yamaha UD Stomp",
    "A.H. Swell Long": "Allan Holdsworth's use of the Yamaha UD Stomp",
    "A.H. Swell Short": "Allan Holdsworth's use of the Yamaha UD Stomp",
    "Aerosol": "a chorus preset in the Lexicon MPX 1",
    "Aurora Delay": "Keeley HALO Andy Timmons delay pedal (Robert Keeley "
                    "personally offered insight and a diagram)",
    "PCM Circular": "Lexicon PCM",
    "PCM Pan": "Lexicon PCM",
    "Space Tape": "Roland Space Echo tape delay",
    "Quad Chorus": "the Quad Chorus block of previous-generation Fractal "
                   "hardware",
    "Strange Things": "themed on the TV series (not a gear model)",
}

# Ordinals proven on hardware, never guessed:
KNOWN_ORDINALS = {
    "multitap": {
        # MULTITAP_BASETYPE (pid 0) ordinal 1 = Aurora Delay, verified on
        # FM9 fw 11.00 by setting it and confirming by ear against preset
        # 509 (2026-08-19/20 sessions)
        "Aurora Delay": 1,
    },
    "pitch": {
        # wire-verified 2026-08-21 with Moncy reading the front panel
        "Dual Detune": 0,
        "Dual Chromatic": 2,
    },
}


def main() -> None:
    out = {
        "schema_version": 1,
        "device": "FM9",
        "content": "facts-only real-world references for delay and chorus "
                   "type NAMES; the catalog has no type rosters so this is "
                   "name-keyed and cannot drift-check against ordinals",
        "keyed_by": "Fractal type display name",
        "source": "Fractal wiki 'Delay block' and 'Chorus block' pages, "
                  "human-pasted 2026-08-21; facts only, no prose",
        "generated_by": "tools/build_effect_type_models.py",
        "warning": "ordinal mapping requires hardware (issue #5); never "
                   "assume the wiki's list order matches the enum order",
        "delay_types": DELAY,
        "chorus_types": CHORUS,
        "multitap_types": MULTITAP,
        "known_ordinals": KNOWN_ORDINALS,
    }
    dest = ROOT / "config" / "effect_type_models.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"delay: {len(DELAY)}, chorus: {len(CHORUS)}, "
          f"multitap: {len(MULTITAP)} mapped -> {dest}")

if __name__ == "__main__":
    sys.exit(main())
