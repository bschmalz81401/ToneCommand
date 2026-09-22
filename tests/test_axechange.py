"""Issue #160 (J7): one Axe-Change link, one preset. Recorded fixtures
only: an FM9 page (11380, 'Deluxe Jag'), an Axe-FX III page (11000) and
the FM9 download. No network (fetchers faked; the conftest guard covers
the gallery's downloader, and this module fakes its own).
"""
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import axechange as ax
from fm9 import gallery
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"
PAGE_FM9 = (FX / "axechange_11380.html").read_text(encoding="utf-8")
PAGE_AXE = (FX / "axechange_11000.html").read_text(encoding="utf-8")
SYX_FM9 = (FX / "axechange_11380.bin").read_bytes()
UI = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
LINK = "https://axechange.fractalaudio.com/detail.php?preset=11380"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(url):
        raise AssertionError(f"a test reached the network for {url}")
    monkeypatch.setattr(ax, "_get", refuse)


# --- parse -------------------------------------------------------------------

def test_parse_url_accepts_one_shape_only():
    assert ax.parse_url(LINK) == 11380
    assert ax.parse_url("http://axechange.fractalaudio.com/detail.php?preset=7&x=1") == 7
    for bad in ("https://example.com/detail.php?preset=1",
                "https://axechange.fractalaudio.com/search.php?q=x",
                "https://axechange.fractalaudio.com/detail.php?preset=abc",
                "not a url", ""):
        with pytest.raises(ax.AxeChangeError):
            ax.parse_url(bad)
    assert ax.is_axechange_link(LINK) and not ax.is_axechange_link("hello")


def test_parse_page_reads_the_details_block():
    d = ax.parse_page(PAGE_FM9)
    assert (d.id, d.name, d.author) == (11380, "Deluxe Jag", "Burgs")
    assert d.product == "FM9" and d.firmware == "8.x"
    assert d.setup and d.line.startswith("Deluxe Jag by Burgs, for FM9, firmware 8.x, ")
    a = ax.parse_page(PAGE_AXE)
    assert a.name == "Nick's Fleetwood Dreams (Ext 1)" and a.product == "Axe-FX III"
    assert a.firmware == "25.x" and a.setup == "Direct"
    assert a.description == "Fleetwood Mac - Dreams Lead" and a.band == "Fleetwood Mac"
    assert a.line == ("Nick's Fleetwood Dreams (Ext 1) by nickynoshoes86, for Axe-FX III, "
                      "firmware 25.x, Direct: Fleetwood Mac - Dreams Lead")


def test_parse_page_markup_change_is_one_line():
    for broken in ("<html><body>nothing here</body></html>",
                   PAGE_FM9.replace('class="details"', 'class="detailz"'),
                   PAGE_FM9.replace("<span>Name</span>", "<span>Title</span>"),
                   ""):
        with pytest.raises(ax.AxeChangeError, match="could not read that page"):
            ax.parse_page(broken)


# --- device and firmware -------------------------------------------------------

def test_device_gate_refuses_another_product_and_old_firmware():
    fm9 = ax.parse_page(PAGE_FM9)
    axe = ax.parse_page(PAGE_AXE)
    assert ax.gate(fm9, "fm9", "12.00") is None
    assert ax.gate(fm9, "fm9", "8.02") is None                 # same major
    assert ax.gate(fm9, "fm9", "7.01") == \
        "this preset needs FM9 firmware 8.x or newer; this unit runs 7.01"
    line = ax.gate(axe, "fm9", "12.00")
    assert line.startswith("this preset is for the Axe-FX III, not the FM9")
    assert ax.gate(fm9, None, "12.00") == gallery.NO_DEVICE_LINE
    assert "could not read this unit's firmware" in ax.gate(fm9, "fm9", "")


# --- fetch --------------------------------------------------------------------

def test_fetch_preset_validates_and_caches(tmp_path):
    calls = []

    def fake(url):
        calls.append(url)
        assert url == "https://axechange.fractalaudio.com/download.php?preset=11380"
        return SYX_FM9
    data = ax.fetch_preset(11380, fetch=fake, cache=tmp_path)
    assert data == SYX_FM9 and (tmp_path / "11380.syx").exists()
    ax.fetch_preset(11380, fetch=fake, cache=tmp_path)
    assert len(calls) == 1                                    # cached
    with pytest.raises(ax.AxeChangeError, match="not a preset file"):
        ax.fetch_preset(11000, fetch=lambda u: b"<html>login</html>", cache=tmp_path)
    assert not (tmp_path / "11000.syx").exists()

    def offline(u):
        raise urllib.error.URLError("no route")
    with pytest.raises(ax.AxeChangeError, match="could not reach Axe-Change"):
        ax.fetch_page(5, fetch=offline)


# --- the route and the bar -------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("TONECOMMAND_STORE_SLOTS", "139-141")
    monkeypatch.setenv("TONECOMMAND_CACHE_DIR", str(tmp_path / "cache"))
    pages = {11380: PAGE_FM9, 11000: PAGE_AXE}
    monkeypatch.setattr(ax, "fetch_page", lambda pid, fetch=None: pages[pid])
    monkeypatch.setattr(ax, "fetch_preset",
                        lambda pid, fetch=None, cache=None: SYX_FM9)
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    monkeypatch.setattr(server, "_install_cache", {})
    return TestClient(server.app)


def test_route_reads_the_page_then_installs_through_the_guarded_path(client):
    r = client.post("/api/axechange", json={"url": LINK})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["line"].startswith("Deluxe Jag by Burgs, for FM9, firmware 8.x")
    assert d["preset"]["name"] == "Deluxe Jag" and d["presets"][0]["hash"] == d["preset"]["hash"]
    out = client.post("/api/install", json={"hash": d["preset"]["hash"], "slot": 140}).json()
    assert out["ok"] is True and out["read_back"] == "Deluxe Jag"


def test_route_refusals_are_one_line(client, monkeypatch):
    r = client.post("/api/axechange", json={"url": "https://example.com/x"})
    assert r.status_code == 400
    r = client.post("/api/axechange",
                    json={"url": "https://axechange.fractalaudio.com/detail.php?preset=11000"})
    assert r.status_code == 409 and "for the Axe-FX III, not the FM9" in r.json()["error"]
    assert server._install_cache == {}
    monkeypatch.setattr(server, "_connected_for_gallery", lambda: (None, ""))
    r = client.post("/api/axechange", json={"url": LINK})
    assert r.status_code == 409 and r.json()["error"] == gallery.NO_DEVICE_LINE


def test_the_bar_routes_a_pasted_link_to_the_page_reader():
    assert "async function runAxeChange(url)" in UI
    assert "fetch('/api/axechange'" in UI
    assert "axechange\\.fractalaudio\\.com\\/detail\\.php\\?preset=\\d+" in UI
    assert "renderAcquirePlan(d);" in UI[UI.index("async function runAxeChange"):
                                          UI.index("function renderAcquirePlan(d)")]
