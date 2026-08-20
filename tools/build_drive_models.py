#!/usr/bin/env python3
"""Generate config/drive_models.json from Yek's Guide to the Fractal Audio
Drive Models (community PDF; publicly mirrored, not redistributed here).

Usage:
    python tools/build_drive_models.py <drive_guide.pdf or extracted .txt>

Extracts FACTS ONLY: which real pedal each Fractal drive model is based on,
taken from the guide's own section headings of the form
"Rat Dist and Fat Rat (based on two versions of the Pro Co RAT)". The
guide's prose is not reproduced. Models the guide does not cover (the FM9
roster grew ~50 entries after the guide's last update) stay unmapped:
never invent a mapping.

Each record carries the Fractal roster name it was built against;
fm9/registry.py refuses to load the sidecar if the catalog roster no
longer matches (DriveModelsStale), so a renumbering fails loudly.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fm9.registry import Registry  # noqa: E402

# Guide-era names that drifted from today's FM9 roster names.
ALIASES = {
    "Treb Boost": ["Treble Boost"],
    "Tube Drv": ["Tube Drive 3-Knob", "Tube Drive 4-Knob", "Tube Drive 5-Knob"],
}

# Corrections and additions with provenance go here, not in the JSON.
OVERRIDES: dict[str, str] = {
    # roster name -> real pedal, each with a citable source:
    # Fractal forum official thread "Fractal Audio DRIVE models: Tone of
    # Kings (based on King of Tone, aka KoT)" (thread 148207)
    "Tone of Kings": "Analogman King of Tone",
    # Forum DRIVE-models index thread 122838 (yek), titles harvested from a
    # human-pasted copy 2026-08-21:
    #   "Blackglass B7K (based on Darkglass B7K)"
    "Blackglass 7K": "Darkglass B7K",
    #   "Treb Boost (based on Dallas Rangemaster)" - upgrades the PDF's
    #   generic "a treble booster"
    "Treble Boost": "Dallas Rangemaster",
    #   "Ruckus (based on Suhr Riot)" - the FM9 renamed Ruckus to the three
    #   Suhr Riot clipping variants
    "Suhr Riot Ge (R)": "Suhr Riot (germanium clipping)",
    "Suhr Riot LED (L)": "Suhr Riot (LED clipping)",
    "Suhr Riot LED/Si (M)": "Suhr Riot (LED/silicon clipping)",
}

HEADING = re.compile(
    r'^\s*([A-Z][A-Za-z0-9 +/\-\.\'’#&,]{2,60}?)\s*'
    r'\((?:possibly )?based on ([^)]{3,120})\)\s*\.*\s*$', re.M)


def extract_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join(pg.extract_text() or "" for pg in PdfReader(str(path)).pages)
    return path.read_text()


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main(source: Path) -> None:
    text = extract_text(source)
    headings: dict[str, str] = {}
    for m in HEADING.finditer(text):
        headings.setdefault(m.group(1).strip(), m.group(2).strip())
    reg = Registry()
    roster = {int(k): str(v) for k, v in reg.drive_roster.items()}
    mapped: dict[str, dict] = {}

    def map_name(part: str, based_on: str, title: str):
        pn = norm(part)
        if not pn:
            return
        for o, name in roster.items():
            rn = norm(name)
            if pn == rn or pn in rn or rn in pn:
                mapped[str(o)] = {"fractal": name, "model": based_on,
                                  "guide_title": title}

    for title, based_on in headings.items():
        for part in ALIASES.get(title, re.split(r"\s+and\s+|,\s*", title)):
            map_name(part, based_on, title)
    for name, based_on in OVERRIDES.items():
        for o, rname in roster.items():
            if rname == name:
                mapped[str(o)] = {"fractal": rname, "model": based_on,
                                  "guide_title": "OVERRIDE"}
    out = {
        "schema_version": 1,
        "device": "FM9",
        "content": "facts-only mapping of drive roster ordinals to the real pedals they model",
        "keyed_by": "FM9_DRIVE_ROSTER ordinal",
        "source": "Yek's Guide to the Fractal Audio Drive Models (community PDF); facts only, no prose reproduced",
        "generated_by": "tools/build_drive_models.py",
        "warning": "generated; corrections belong in the generator's OVERRIDES table",
        "drives": {k: mapped[k] for k in sorted(mapped, key=int)},
    }
    dest = Path(__file__).resolve().parent.parent / "config" / "drive_models.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    unmapped = [n for o, n in sorted(roster.items()) if str(o) not in mapped]
    print(f"mapped {len(mapped)}/{len(roster)} drive roster entries -> {dest}")
    print(f"unmapped ({len(unmapped)}), left without a mapping by design:")
    for n in unmapped:
        print("  -", n)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(Path(sys.argv[1]))
