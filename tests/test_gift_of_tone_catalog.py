"""#154 (J1): the curated Gift of Tone catalog validates, counts match the
page as read, the known bundles carry their cab banks, the site publishes it
and the app reads it site-first with fallbacks."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fm9 import gift_of_tone  # noqa: E402

CATALOG = json.loads((ROOT / "catalog" / "gift_of_tone.json").read_text(encoding="utf-8"))
KINDS = {"preset", "preset plus cab bundle", "cab only", "effect blocks"}
SHA = re.compile(r"^[0-9a-f]{64}$")


def test_every_entry_validates_against_the_schema():
    assert CATALOG["source"] == "https://www.fractalaudio.com/gift-of-tone/" and CATALOG["page_read"] == "2026-09-19"
    ids = set()
    for e in CATALOG["entries"]:
        assert set(e) == {"id", "artists", "year", "number", "kind", "description", "devices", "url", "sha256", "bytes", "contents"}, e["id"]
        assert e["id"] not in ids; ids.add(e["id"])
        assert e["artists"] and all(isinstance(a, str) and a for a in e["artists"])
        assert e["year"] in (2022, 2023, 2024) and re.fullmatch(r"\d+(\.5)?", e["number"])
        assert e["kind"] in KINDS and e["description"]
        assert e["devices"] and all(d["device"] and d["min_firmware"] for d in e["devices"])
        assert e["url"].startswith("https://www.fractalaudio.com/downloads/misc/_gift2") and e["url"].endswith(".zip")
        assert SHA.match(e["sha256"]) and e["bytes"] > 1000
        c = e["contents"]
        assert set(c) == {"presets", "cabs", "bundles", "blocks", "other"}
        assert c["presets"] or c["cabs"] or c["bundles"] or c["blocks"], f"{e['id']} lists no preset, cab, bundle or block"
        if e["kind"] == "effect blocks":
            assert c["blocks"] and all(b.lower().endswith(".blk") for b in c["blocks"]) and not c["presets"]
        for b in c["bundles"]:
            assert b["file"].lower().endswith(".fasbundle") and b["map"] is not None, b["file"]
            assert b["map"]["presets"] and all(p["location"] and p["name"] and p["file"] for p in b["map"]["presets"])
            for cab in b["map"]["cabs"]:
                assert cab["bank"] and cab["number"] and cab["name"] and cab["file"]
    assert len(CATALOG["entries"]) == 34


def test_counts_per_year_match_the_page_as_read():
    # 2022 lists 26 numbered entries plus the 16.5 touring-band post; two zips (19, 20) carry two artists each
    years = {y: sum(1 for e in CATALOG["entries"] if e["year"] == y) for y in (2022, 2023, 2024)}
    assert years == {2022: 27, 2023: 6, 2024: 1} and CATALOG["counts"] == {"2022": 27, "2023": 6, "2024": 1}
    shared = [e for e in CATALOG["entries"] if len(e["artists"]) == 2]
    assert sorted(e["number"] for e in shared) == ["16.5", "19", "20"]
    urls = [e["url"] for e in CATALOG["entries"]]
    assert len(urls) == len(set(urls)), "nothing is listed twice"


def test_the_three_known_bundles_carry_their_cab_bank_and_number():
    by = {e["id"]: e for e in CATALOG["entries"]}
    hauch = by["got-2023-3"]
    maps = {b["file"]: b["map"] for b in hauch["contents"]["bundles"]}
    assert "WHA440-FM9.fasBundle" in maps and "WH-A440-III.fasBundle" in maps
    m = maps["WH-A440-III.fasBundle"]
    assert m["device"] == {"deviceId": "16", "major": "23", "minor": "5"}
    assert m["presets"] == [{"location": "389", "name": "WHA440", "file": "WHA440.syx"}]
    assert m["cabs"] == [{"bank": "2", "number": "79", "name": "Rhythm Match", "file": "cabs/Rhythm Match.syx"}]
    assert any(c.endswith("Rhythm Match.syx") for c in hauch["contents"]["cabs"])  # the loose cab beside the bundles
    townsend = by["got-2022-14"]
    assert [b["file"] for b in townsend["contents"]["bundles"]] and all(b["map"]["cabs"] for b in townsend["contents"]["bundles"])
    periphery = by["got-2024-1"]
    assert len(periphery["contents"]["presets"]) == 9 and periphery["contents"]["bundles"] == []
    assert sorted({p.split("/")[0] for p in periphery["contents"]["presets"]}) == ["Axe-FX III", "FM3", "FM9"]
    assert periphery["devices"] == [{"device": "Axe-Fx III", "min_firmware": "27.x"}, {"device": "FM9", "min_firmware": "8"}, {"device": "FM3", "min_firmware": "9"}]


def test_the_catalog_holds_no_file_bytes_only_names_hashes_and_maps():
    raw = (ROOT / "catalog" / "gift_of_tone.json").read_bytes()
    assert len(raw) < 200_000 and b"PK\x03\x04" not in raw and b"F0 00" not in raw
    assert "policy" in CATALOG and "nothing mirrored" in CATALOG["policy"]


def test_the_site_publishes_the_catalog_with_cors():
    src = (ROOT / "site" / "build.py").read_text(encoding="utf-8")
    assert 'shutil.copy(catalog, DIST / "gift-of-tone.json")' in src
    assert '"/gift-of-tone.json\\n  Access-Control-Allow-Origin: *' in src


def test_the_app_reads_site_first_then_github_then_local(monkeypatch):
    calls = []
    good = {"entries": [{"url": "https://www.fractalaudio.com/downloads/misc/_gift24/x.zip", "sha256": "a" * 64}]}

    def fake_get(url, timeout, accept="application/json"):
        calls.append(url)
        if url.startswith("https://tonecommand.com/"):
            return good
        raise AssertionError("github should not be asked when the site answers")
    monkeypatch.setattr(gift_of_tone, "_get_json", fake_get)
    monkeypatch.setattr(gift_of_tone, "_cache", {"doc": None, "at": 0.0, "source": None})
    doc, source, why = gift_of_tone.fetch()
    assert doc == good and source == "site" and why is None and calls == ["https://tonecommand.com/gift-of-tone.json"]
    # site down: the repository's contents API
    calls.clear()

    def site_down(url, timeout, accept="application/json"):
        calls.append(url)
        if url.startswith("https://tonecommand.com/"):
            raise OSError("site unreachable")
        if "api.github.com" in url:
            return {"download_url": "https://raw.githubusercontent.com/x/catalog/gift_of_tone.json"}
        return good
    monkeypatch.setattr(gift_of_tone, "_get_json", site_down)
    monkeypatch.setattr(gift_of_tone, "_cache", {"doc": None, "at": 0.0, "source": None})
    doc, source, why = gift_of_tone.fetch()
    assert doc == good and source == "github" and calls[1].startswith("https://api.github.com/repos/monzta1/ToneCommand/contents/catalog/gift_of_tone.json")
    # everything down: the local checkout's copy, which is the real catalog
    monkeypatch.setattr(gift_of_tone, "_get_json", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(gift_of_tone, "_cache", {"doc": None, "at": 0.0, "source": None})
    doc, source, why = gift_of_tone.fetch()
    assert source == "local" and len(doc["entries"]) == 34 and why is None
    # a site answer of the wrong shape is refused, not trusted
    monkeypatch.setattr(gift_of_tone, "_get_json", lambda url, timeout, accept="application/json": {"entries": [{"url": "https://evil.example/x.zip", "sha256": "a" * 64}]} if "tonecommand" in url else (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(gift_of_tone, "_cache", {"doc": None, "at": 0.0, "source": None})
    doc, source, why = gift_of_tone.fetch()
    assert source == "local"
