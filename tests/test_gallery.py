"""Issues #156 (J3) and #157 (J4): the Gift of Tone gallery's device gate and
its verified, click-time fetch. No network anywhere in here: fetchers are
fakes, the catalog is a three-entry fixture, zips are built in the test.
"""
import hashlib
import io
import sys
import urllib.error
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import acquire, gallery, gift_of_tone
from fm9.sim import SimFM9
from tests.test_cabs import make_cab
from tests.test_install import make_file

FRACTAL = "https://www.fractalaudio.com/downloads/misc/_gift22/"


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()


def _entry(eid, artists, devices, url_name, data, contents):
    return {"id": eid, "artists": artists, "year": 2022, "number": "1",
            "kind": "preset", "description": "x",
            "devices": devices, "url": FRACTAL + url_name,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "contents": {"presets": [], "cabs": [], "bundles": [],
                         "blocks": [], "other": [], **contents}}


FM9_ONLY = [{"device": "FM9", "min_firmware": "4.0"}]
ALL = [{"device": "All devices"}]
AXE_ONLY = [{"device": "Axe-Fx III", "min_firmware": "21"}]
THREE = [{"device": "Axe-Fx III", "min_firmware": "21"},
         {"device": "FM9", "min_firmware": "12.02"},
         {"device": "FM3", "min_firmware": "6.0"}]


@pytest.fixture
def fixture():
    """Three entries: FM9-only, all devices, Axe-Fx III-only; plus one whose
    FM9 minimum is newer than the simulator's firmware."""
    z1 = _zip([("Fm9 Only GoT.syx", make_file(name="Fm9 Only"))])
    z2 = _zip([("Cabs/Anyone.syx", make_cab()), ("ReadMe.txt", b"hi")])
    z3 = _zip([("Axe Only.syx", make_file(name="Axe Only"))])
    z4 = _zip([("New Fw.syx", make_file(name="New Fw"))])
    entries = [
        _entry("got-fm9", ["Fm9 Person"], FM9_ONLY, "fm9.zip", z1,
               {"presets": ["Fm9 Only GoT.syx"]}),
        _entry("got-all", ["Cab Person"], ALL, "all.zip", z2,
               {"cabs": ["Cabs/Anyone.syx"], "other": ["ReadMe.txt"]}),
        _entry("got-axe", ["Axe Person"], AXE_ONLY, "axe.zip", z3,
               {"presets": ["Axe Only.syx"]}),
        _entry("got-new", ["Future Person"], THREE, "new.zip", z4,
               {"presets": ["New Fw.syx"]}),
    ]
    return {"entries": entries, "zips": {"fm9.zip": z1, "all.zip": z2,
                                         "axe.zip": z3, "new.zip": z4}}


# --- J3: device and firmware ---------------------------------------------

def test_entries_for_the_device_drop_what_it_cannot_take(fixture):
    ids = lambda es: [e["id"] for e in es]
    assert ids(gallery.entries_for(fixture["entries"], "fm9")) == \
        ["got-fm9", "got-all", "got-new"]
    # no device: browse the whole catalog
    assert ids(gallery.entries_for(fixture["entries"], None)) == \
        ["got-fm9", "got-all", "got-axe", "got-new"]
    # a device kind the catalog has no name for browses like no device
    assert ids(gallery.entries_for(fixture["entries"], "headrush")) == \
        ["got-fm9", "got-all", "got-axe", "got-new"]
    assert gallery.device_name("fm9") == "FM9"
    assert gallery.device_name("headrush") is None


def test_the_firmware_gate_is_one_line_or_nothing(fixture):
    e = fixture["entries"]
    assert gallery.firmware_gate(e[0], "fm9", "12.00") is None
    assert gallery.firmware_gate(e[0], "fm9", "4.0") is None       # equal
    line = gallery.firmware_gate(e[0], "fm9", "3.06")
    assert line == ("this pack needs FM9 firmware 4.0 or newer; this unit "
                    "runs 3.06")
    assert gallery.firmware_gate(e[3], "fm9", "12.00") == \
        "this pack needs FM9 firmware 12.02 or newer; this unit runs 12.00"
    assert gallery.firmware_gate(e[3], "fm9", "12.02") is None
    assert gallery.firmware_gate(e[1], "fm9", "1.0") is None        # no minimum
    assert gallery.firmware_gate(e[2], "fm9", "12.00") == \
        "this pack has no FM9 version"
    assert gallery.firmware_gate(e[0], None, "12.00") == gallery.NO_DEVICE_LINE


def test_an_unreadable_firmware_label_refuses_too(fixture):
    e = fixture["entries"][0]
    for label in ("", None, "unknown"):
        line = gallery.firmware_gate(e, "fm9", label)
        assert line and "could not read this unit's firmware" in line
        assert "4.0" in line and "nothing is installed" in line
    assert gallery.parse_version("12.00") == (12, 0)
    assert gallery.parse_version("4.0") == (4, 0)
    assert gallery.parse_version("v21.1.3") == (21, 1, 3)
    assert gallery.parse_version("") is None
    assert gallery.parse_version("soon") is None


@pytest.fixture
def client(fixture, monkeypatch, tmp_path):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "134-149")
    monkeypatch.setenv("TONECOMMAND_CAB_SLOTS", "0-15")
    monkeypatch.setenv("TONECOMMAND_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(gift_of_tone, "fetch",
                        lambda timeout=6.0: ({"entries": fixture["entries"]},
                                             "fixture", None))
    calls = []

    def fake_fetch(url):
        calls.append(url)
        name = url.rsplit("/", 1)[-1]
        if name not in fixture["zips"]:
            raise urllib.error.HTTPError(url, 404, "nope", {}, None)
        return fixture["zips"][name]
    monkeypatch.setattr(gallery, "_download", fake_fetch)
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    monkeypatch.setattr(server, "_install_cache", {})
    c = TestClient(server.app)
    c.calls = calls
    return c


def test_the_gallery_route_filters_for_the_connected_unit(client):
    d = client.get("/api/gift-of-tone").json()
    assert d["device"] == "fm9" and d["firmware"] == "11.00"
    assert [e["id"] for e in d["entries"]] == ["got-fm9", "got-all", "got-new"]
    assert d["hidden"] == 1 and d["note"] is None
    assert d["entries"][0]["min_firmware"] == "4.0"
    assert d["entries"][1]["min_firmware"] is None


def test_no_device_still_browses_with_one_line(client, monkeypatch):
    monkeypatch.setattr(server, "_connected_for_gallery", lambda: (None, ""))
    r = client.get("/api/gift-of-tone")
    assert r.status_code == 200
    d = r.json()
    assert d["device"] is None and len(d["entries"]) == 4
    assert d["note"] == gallery.NO_DEVICE_LINE
    r = client.post("/api/gift-of-tone/fetch", json={"id": "got-fm9"})
    assert r.status_code == 409 and r.json()["error"] == gallery.NO_DEVICE_LINE
    assert client.calls == []


def test_too_old_firmware_is_one_line_and_no_fetch(client, monkeypatch):
    r = client.post("/api/gift-of-tone/fetch", json={"id": "got-new"})
    assert r.status_code == 409
    assert r.json()["error"] == ("this pack needs FM9 firmware 12.02 or "
                                 "newer; this unit runs 11.00")
    assert client.calls == []
    r = client.post("/api/gift-of-tone/fetch", json={"id": "got-axe"})
    assert r.status_code == 409 and "no FM9 version" in r.json()["error"]


# --- J4: fetch, verify, cache, unpack -------------------------------------

def test_fetch_verifies_the_hash_before_anything_else(fixture, tmp_path):
    e = fixture["entries"][0]
    data, source = gallery.fetch_entry(e, fetch=lambda u: fixture["zips"]["fm9.zip"],
                                       cache=tmp_path)
    assert source == "fetched" and data == fixture["zips"]["fm9.zip"]
    assert (tmp_path / f"{e['sha256']}.zip").exists()


def test_a_hash_mismatch_refuses_and_caches_nothing(fixture, tmp_path):
    e = fixture["entries"][0]
    changed = fixture["zips"]["fm9.zip"] + b"\x00"
    with pytest.raises(gallery.GalleryError, match="changed since it was catalogued"):
        gallery.fetch_entry(e, fetch=lambda u: changed, cache=tmp_path)
    assert list(tmp_path.glob("*")) == []


def test_fetch_failures_are_one_line_each(fixture, tmp_path):
    e = fixture["entries"][0]

    def offline(u):
        raise urllib.error.URLError("no route to host")
    with pytest.raises(gallery.GalleryError, match="could not reach fractalaudio.com"):
        gallery.fetch_entry(e, fetch=offline, cache=tmp_path)

    def gone(u):
        raise urllib.error.HTTPError(u, 404, "gone", {}, None)
    with pytest.raises(gallery.GalleryError, match="answered 404"):
        gallery.fetch_entry(e, fetch=gone, cache=tmp_path)
    mirrored = dict(e, url="https://tonecommand.com/mirror/fm9.zip")
    with pytest.raises(gallery.GalleryError, match="only fetches from the source"):
        gallery.fetch_entry(mirrored, fetch=lambda u: b"", cache=tmp_path)
    unhashed = dict(e, sha256="")
    with pytest.raises(gallery.GalleryError, match="no sha256"):
        gallery.fetch_entry(unhashed, fetch=lambda u: b"", cache=tmp_path)


def test_the_cache_serves_a_verified_copy_without_fetching(fixture, tmp_path):
    e = fixture["entries"][0]
    calls = []

    def fetch(u):
        calls.append(u)
        return fixture["zips"]["fm9.zip"]
    gallery.fetch_entry(e, fetch=fetch, cache=tmp_path)
    data, source = gallery.fetch_entry(e, fetch=fetch, cache=tmp_path)
    assert source == "cached" and len(calls) == 1
    # a damaged cache file is discarded and the source fetched again
    (tmp_path / f"{e['sha256']}.zip").write_bytes(b"junk")
    data, source = gallery.fetch_entry(e, fetch=fetch, cache=tmp_path)
    assert source == "fetched" and len(calls) == 2 and data == fixture["zips"]["fm9.zip"]


def test_unpack_matches_exact_catalog_paths_and_skips_the_rest(fixture):
    e = fixture["entries"][1]                       # cabs + a readme
    stray = _zip([("Cabs/Anyone.syx", make_cab()), ("ReadMe.txt", b"hi"),
                  ("Extras/Anyone.syx", make_cab()),   # a listed NAME elsewhere
                  ("__MACOSX/._Anyone.syx", b"junk"), ("Bonus/preset.syx", b"x")])
    members, unexpected = gallery.unpack(e, stray)
    assert [(m["path"], m["kind"]) for m in members] == \
        [("Cabs/Anyone.syx", "cabs"), ("ReadMe.txt", "other")]
    assert unexpected == ["Extras/Anyone.syx", "Bonus/preset.syx"]
    with pytest.raises(gallery.GalleryError, match="not a readable zip"):
        gallery.unpack(e, b"not a zip")


def test_the_fetch_route_end_to_end(client, fixture):
    r = client.post("/api/gift-of-tone/fetch", json={"id": "got-fm9"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["verified"] is True and d["source"] == "fetched"
    assert d["sha256"] == hashlib.sha256(fixture["zips"]["fm9.zip"]).hexdigest()
    assert client.calls == [FRACTAL + "fm9.zip"]
    assert [p["name"] for p in d["presets"]] == ["Fm9 Only"]
    assert d["unexpected"] == [] and d["skipped"] == []
    # and it installs through the guarded path, read back
    out = client.post("/api/install", json={"hash": d["presets"][0]["hash"],
                                            "slot": 140}).json()
    assert out["ok"] is True and out["read_back"] == "Fm9 Only"
    # second click: served from the cache, no fetch
    n = len(client.calls)
    d2 = client.post("/api/gift-of-tone/fetch", json={"id": "got-fm9"}).json()
    assert d2["source"] == "cached" and len(client.calls) == n
    # cabs in an all-devices entry arrive as installable cabs; the readme
    # is listed content, not installable, and not "unexpected"
    d3 = client.post("/api/gift-of-tone/fetch", json={"id": "got-all"}).json()
    assert [c["file"] for c in d3["cabs"]] == ["Anyone.syx"]
    assert d3["unexpected"] == []
    r = client.post("/api/gift-of-tone/fetch", json={"id": "nope"})
    assert r.status_code == 404
    r = client.post("/api/gift-of-tone/fetch", json={})
    assert r.status_code == 400


def test_the_fetch_route_refuses_a_changed_file(client, fixture, monkeypatch):
    monkeypatch.setattr(gallery, "_download",
                        lambda u: fixture["zips"]["fm9.zip"] + b"!")
    r = client.post("/api/gift-of-tone/fetch", json={"id": "got-fm9"})
    assert r.status_code == 502
    assert "changed since it was catalogued" in r.json()["error"]
    assert server._install_cache == {}


def test_acquire_uses_the_verified_catalog_path_first(client, monkeypatch):
    monkeypatch.setattr(acquire, "search_local", lambda q: [])
    scraped = []
    monkeypatch.setattr(acquire, "catalog", lambda: scraped.append(1) or [])
    d = client.post("/api/acquire", json={
        "query": "get me the fm9 person tones from gift of tone to preset 3"}).json()
    assert d["verified"] is True and d["id"] == "got-fm9"
    assert d["target_editor"] == 3
    assert [p["name"] for p in d["presets"]] == ["Fm9 Only"]
    assert scraped == []                          # no page scrape needed
    # an artist the catalog does not have falls back to the scrape
    monkeypatch.setattr(acquire, "catalog",
                        lambda: [{"artist": "Someone Else", "year": 2021,
                                  "url": FRACTAL + "x.zip"}])
    monkeypatch.setattr(acquire, "fetch_bundle",
                        lambda url, fetch=None: ([], [], ["nothing"]))
    r = client.post("/api/acquire", json={"query": "get the someone else tones"})
    assert r.status_code == 200 and r.json()["artist"] == "Someone Else"
    assert "verified" not in r.json()


def test_acquire_on_the_catalog_path_honours_the_firmware_gate(client, monkeypatch):
    monkeypatch.setattr(acquire, "search_local", lambda q: [])
    r = client.post("/api/acquire", json={"query": "get the future person tones"})
    assert r.status_code == 409
    assert "12.02" in r.json()["error"]
    assert client.calls == []
