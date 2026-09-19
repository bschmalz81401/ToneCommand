#!/usr/bin/env python3
"""Build catalog/gift_of_tone.json from Fractal's Gift of Tone page facts and
the zips' own contents (issue #154, J1).

The SEED table below is the page as read on 2026-09-19
(https://www.fractalaudio.com/gift-of-tone/): artist, year, series number,
Fractal's one-line description, kind, and the device matrix exactly as the
page states it. This tool adds what only the files can say: each zip's sha256
and size, the preset and cab .syx names inside, and every .fasBundle's bundle
map (device id and firmware, preset Location and Name, each cab's Bank,
Number and Name), and the .blk effect blocks of the block gifts. It downloads each zip ONCE into a temporary directory to
read it and keeps nothing but the catalog: URLs, hashes and names. Nothing is
mirrored (kb/SITE.md, "Fractal sources: terms and fetch policy").

    .venv/bin/python tools/gift_of_tone_catalog.py            # rebuild
    .venv/bin/python tools/gift_of_tone_catalog.py --check    # re-download and compare hashes

Re-read the page when it changes; the seed is hand-transcribed and the tool
cannot verify prose, only files.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tempfile
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "catalog" / "gift_of_tone.json"
PAGE = "https://www.fractalaudio.com/gift-of-tone/"
BASE = "https://www.fractalaudio.com/downloads/misc/"
PAGE_READ = "2026-09-19"
UA = "ToneCommand gift-of-tone-catalog (https://tonecommand.com)"
MAX_ZIP_BYTES = 64 * 1024 * 1024

# Device matrix strings exactly as the page states them, shared by most entries.
D21 = [{"device": "Axe-Fx III", "min_firmware": "21"}, {"device": "FM9", "min_firmware": "4.0"}, {"device": "FM3", "min_firmware": "6.0"}]
D2305 = [{"device": "Axe-Fx III", "min_firmware": "23.05"}, {"device": "FM9", "min_firmware": "5.0"}, {"device": "FM3", "min_firmware": "7.0"}]
D23 = [{"device": "Axe-Fx III", "min_firmware": "23"}, {"device": "FM9", "min_firmware": "5.0"}, {"device": "FM3", "min_firmware": "7.0"}]

#: (year, number, artists, kind, description, devices, zip file name)
SEED = [
    (2022, "1", ["Leon Todd"], "preset plus cab bundle", "Preset + Cab Bundle (3 bundles)", D21, "_gift22/FAS-gift22-01-Leon-Todd.zip"),
    (2022, "2", ["Mark Day"], "preset", "Preset", D21, "_gift22/FAS-gift22-02-Mark-Day.zip"),
    (2022, "3", ["Ryan 'Fluff' Bruce"], "preset", "Preset", [{"device": "Axe-Fx III", "min_firmware": "20.04"}, {"device": "FM9", "min_firmware": "4.0"}, {"device": "FM3", "min_firmware": "6.0"}], "_gift22/FAS-gift22-03-Fluff.zip"),
    (2022, "4", ["Marco Fanton"], "preset", "Preset", D21, "_gift22/FAS-gift22-04-Marco-Fanton.zip"),
    (2022, "5", ["Chris Traynor"], "preset", "Preset", D21, "_gift22/FAS-gift22-05-Chris-Traynor.zip"),
    (2022, "6", ["Mikko Logren"], "cab only", "Cab IR", [{"device": "All devices", "min_firmware": "as stated: All devices"}], "_gift22/FAS-gift22-06-Mikko-Logren.zip"),
    (2022, "7", ["Andy Wood"], "preset", "Preset", [{"device": "Axe-Fx III", "min_firmware": "11"}, {"device": "FM9", "min_firmware": "4.0"}, {"device": "FM3", "min_firmware": "3.02"}], "_gift22/FAS-gift22-07-Andy-Wood.zip"),
    (2022, "8", ["Mark 'Moke' Perry"], "preset", "Preset", D21, "_gift22/FAS-gift22-08-Mark-Moke-Perry.zip"),
    (2022, "9", ["AustinBuddy"], "preset plus cab bundle", "Preset + Cab Bundle (6 presets)", D21, "_gift22/FAS-gift22-09-Austin-Buddy.zip"),
    (2022, "10", ["Cooper Carter"], "preset", "Preset", D21, "_gift22/FAS-gift22-10-Cooper-Carter.zip"),
    (2022, "11", ["Fremen"], "preset", "Preset (3 presets, 8 scenes each)", D21, "_gift22/FAS-gift22-11-Fremen.zip"),
    (2022, "12", ["Justin York"], "preset plus cab bundle", "Preset + Cab Bundle", D21, "_gift22/FAS-gift22-12-Justin-York.zip"),
    (2022, "13", ["Synyster Gates"], "preset plus cab bundle", "Cab IR + Preset Bundle", D21, "_gift22/FAS-gift22-13-Synyster-Gates.zip"),
    (2022, "14", ["Devin Townsend"], "preset plus cab bundle", "Preset + Cab Bundle", D21, "_gift22/FAS-gift22-14-Devin-Townsend.zip"),
    (2022, "15", ["Phil Collen"], "preset plus cab bundle", "Preset + Cab Bundle (FM9/FM3)", D21, "_gift22/FAS-gift22-15-Phil-Collen.zip"),
    (2022, "16", ["Steve Vai"], "effect blocks", "Effect Blocks", D21, "_gift22/FAS-gift22-16-Steve-Vai.zip"),
    (2022, "16.5", ["Phillip Bynoe", "Dante Frisiello"], "preset", "Preset (8 scenes), Steve Vai touring band, one zip for both", D21, "_gift22/FAS-gift22-16p5-Steve-Vai-Touring-Band.zip"),
    (2022, "17", ["Frank Steffen Mueller"], "preset", "Preset (8 scenes)", D21, "_gift22/FAS-gift22-17-Frank-Steffen-Mueller.zip"),
    (2022, "18", ["Jason Richardson"], "preset", "Preset", D21, "_gift22/FAS-gift22-18-Jason-Richardson.zip"),
    (2022, "19", ["Tom Hamilton", "Brad Whitford"], "preset plus cab bundle", "Aerosmith: Tom Hamilton Preset + Cab Bundle (8 scenes), Brad Whitford Preset, one zip for both", D21, "_gift22/FAS-gift22-19-Aerosmith-Hamilton-Whitford.zip"),
    (2022, "20", ["Tosin Abasi", "Javier Reyes"], "preset", "Animals as Leaders: Tosin Abasi Preset (3 scenes), Javier Reyes Preset (8 scenes), one zip for both", D21, "_gift22/FAS-gift22-20-AAL.zip"),
    (2022, "21", ["Guthrie Govan"], "preset", "Preset (4 scenes)", D21, "_gift22/FAS-gift22-21-Guthrie-Govan.zip"),
    (2022, "22", ["John Petrucci"], "effect blocks", "Effect Blocks", D21, "_gift22/FAS-gift22-22-John-Petrucci.zip"),
    (2022, "23", ["Alex Lifeson"], "effect blocks", "Effect Blocks", D21, "_gift22/FAS-gift22-23-Alex-Lifeson.zip"),
    (2022, "24", ["Tim Pierce"], "preset", "Preset (7 scenes)", D21, "_gift22/FAS-gift22-24-Tim-Pierce-v2.zip"),
    (2022, "25", ["Larry Mitchell"], "preset plus cab bundle", "Preset + Cab Bundle (8 scenes, includes backing track)", D21, "_gift22/FAS-gift22-25-Larry-Mitchell.zip"),
    (2022, "26", ["Dweezil Zappa"], "preset plus cab bundle", "Preset + Cab Bundle (the 2022 finale)", D21, "_gift22/FAS-gift22-26-Dweezil-Zappa.zip"),
    (2023, "1", ["Aaron Marshall"], "preset", "Preset (8-scene)", D23, "_gift23/FAS-gift23-01-Aaron-Marshall.zip"),
    (2023, "2", ["Greg Wells"], "preset plus cab bundle", "Preset + Cab IRs", D23, "_gift23/FAS-gift23-02-Greg-Wells.zip"),
    (2023, "3", ["Wes Hauch"], "preset plus cab bundle", "Preset + Cab Bundle", D2305, "_gift23/FAS-gift23-03-Wes-Hauch.zip"),
    (2023, "4", ["Chris Baseford"], "preset", "Preset (8 scenes, multiple versions)", D2305, "_gift23/FAS-gift23-04-Chris_Baseford.zip"),
    (2023, "5", ["Justin Derrico"], "preset plus cab bundle", "Preset + Cab Bundle", D2305, "_gift23/FAS-gift23-05-Justin-Derrico.zip"),
    (2023, "6", ["Neal Schon"], "preset", "Preset (4 scenes), the 2023 finale", D2305, "_gift23/FAS-gift23-06-Neal-Schon.zip"),
    (2024, "1", ["Periphery"], "preset", "Three presets featuring multiple scenes", [{"device": "Axe-Fx III", "min_firmware": "27.x"}, {"device": "FM9", "min_firmware": "8"}, {"device": "FM3", "min_firmware": "9"}], "_gift24/FAS-gift24-01-Periphery.zip"),
]

KINDS = ("preset", "preset plus cab bundle", "cab only", "effect blocks")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(MAX_ZIP_BYTES + 1)
    if len(data) > MAX_ZIP_BYTES:
        raise RuntimeError(f"{url}: over {MAX_ZIP_BYTES} bytes")
    return data


def _junk(name: str) -> bool:
    return name.startswith("__MACOSX/") or name.endswith("/.DS_Store") or name.endswith("/") or "/._" in name or name.startswith("._")


def bundle_map(data: bytes) -> dict | None:
    """The .fasBundle's .bundle XML: device, presets, cabs. None when absent."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            if ".bundle" not in names:
                return None
            xml = z.read(".bundle")
    except zipfile.BadZipFile:
        return None
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None
    dev = root.find("Device")
    out = {
        "version": root.get("version"),
        "device": ({"deviceId": dev.get("deviceId"), "major": dev.get("deviceMajor"), "minor": dev.get("deviceMinor")}
                   if dev is not None else None),
        "presets": [{"location": p.get("Location"), "name": p.get("Name"), "file": p.get("File")} for p in root.findall("Preset")],
        "cabs": sorted({(c.get("Bank"), c.get("Number"), c.get("Name"), c.get("File")) for c in root.findall("CabData")}),
    }
    out["cabs"] = [{"bank": b, "number": n, "name": nm, "file": f} for b, n, nm, f in out["cabs"]]
    return out


def contents(data: bytes) -> dict:
    presets, cabs, bundles, blocks, other = [], [], [], [], []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            name = info.filename
            if _junk(name):
                continue
            base = name.rsplit("/", 1)[-1]
            low = base.lower()
            if low.endswith(".fasbundle"):
                inner = z.read(name)
                entry = {"file": name, "map": bundle_map(inner)}
                with zipfile.ZipFile(io.BytesIO(inner)) as b:
                    entry["members"] = sorted(n for n in b.namelist() if not _junk(n) and n != ".bundle")
                bundles.append(entry)
            elif low.endswith(".syx"):
                (cabs if "cab" in name.lower() or "/cabs/" in name.lower() or low.startswith("u1-") else presets).append(name)
            elif low.endswith(".blk"):
                blocks.append(name)   # effect blocks (the Vai, Petrucci and Lifeson gifts)
            else:
                other.append(name)
    return {"presets": sorted(presets), "cabs": sorted(cabs), "bundles": sorted(bundles, key=lambda b: b["file"]),
            "blocks": sorted(blocks), "other": sorted(other)}


def build(check_only: bool = False) -> int:
    existing = json.loads(CATALOG.read_text()) if CATALOG.exists() else {"entries": []}
    by_url = {e["url"]: e for e in existing.get("entries", [])}
    entries = []
    mismatches = 0
    for year, number, artists, kind, description, devices, path in SEED:
        assert kind in KINDS
        url = BASE + path
        data = fetch(url)
        sha = hashlib.sha256(data).hexdigest()
        if check_only:
            old = by_url.get(url)
            ok = bool(old) and old["sha256"] == sha
            print(f"{'ok  ' if ok else 'DIFF'} {sha[:12]} {path}")
            mismatches += 0 if ok else 1
            continue
        entry = {
            "id": f"got-{year}-{number.replace('.', 'p')}",
            "artists": artists, "year": year, "number": number, "kind": kind,
            "description": description, "devices": devices,
            "url": url, "sha256": sha, "bytes": len(data),
            "contents": contents(data),
        }
        entries.append(entry)
        print(f"{sha[:12]} {len(data):>7} {path}")
    if check_only:
        print(f"{mismatches} mismatch(es)")
        return 1 if mismatches else 0
    doc = {
        "source": PAGE, "page_read": PAGE_READ, "built": date.today().isoformat(),
        "policy": "URLs and hashes only; fetched at click time from fractalaudio.com; nothing mirrored (kb/SITE.md)",
        "counts": {str(y): sum(1 for e in entries if e["year"] == y) for y in (2022, 2023, 2024)},
        "entries": entries,
    }
    CATALOG.parent.mkdir(exist_ok=True)
    CATALOG.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {CATALOG} with {len(entries)} entries")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="re-download every zip and compare its hash to the catalog")
    args = ap.parse_args()
    return build(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
