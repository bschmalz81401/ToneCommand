"""Issue #158 (J5): "sound like Devin Townsend" resolves to the Gift of Tone
catalog deterministically, asks when ambiguous, and is honest when absent.
No network: the catalog is a fixture or the shipped file, fetchers are
guarded by conftest.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import artist_pack, gallery, gift_of_tone
from fm9.sim import SimFM9

ROOT = Path(__file__).resolve().parent.parent
UI = (ROOT / "ui" / "index.html").read_text()
RULES = (ROOT / "config" / "tone_rules.md").read_text()


def _e(eid, artists, year, devices=None, kind="preset", description=None):
    return {"id": eid, "artists": artists, "year": year, "number": "1",
            "kind": kind, "description": description or kind,
            "devices": devices or [{"device": "FM9", "min_firmware": "4.0"}],
            "url": "https://www.fractalaudio.com/x.zip", "sha256": "0" * 64,
            "bytes": 1, "contents": {"presets": [], "cabs": [], "bundles": [],
                                     "blocks": [], "other": []}}


FIXTURE = [
    _e("got-devin", ["Devin Townsend"], 2022, kind="preset plus cab bundle"),
    _e("got-periphery", ["Periphery"], 2024),
    _e("got-markday", ["Mark Day"], 2022),
    _e("got-moke", ["Mark 'Moke' Perry"], 2022),
    _e("got-vai", ["Steve Vai"], 2022, kind="effect blocks"),
    _e("got-york", ["Justin York"], 2022),
    _e("got-derrico", ["Justin Derrico"], 2023),
    _e("got-axe", ["Axe Only"], 2022, devices=[{"device": "Axe-Fx III"}]),
]


# --- the phrase --------------------------------------------------------------

def test_the_four_shapes_yield_the_name():
    assert artist_pack.artist_phrase("sound like Devin Townsend") == "devin townsend"
    assert artist_pack.artist_phrase("Sounds like Devin Townsend please") == "devin townsend"
    assert artist_pack.artist_phrase("give me Devin's rig") == "devin"
    assert artist_pack.artist_phrase("install the Periphery pack") == "periphery"
    assert artist_pack.artist_phrase("install the Periphery pack to preset 4") == "periphery"
    assert artist_pack.artist_phrase("Steve Vai tones") == "steve vai"
    assert artist_pack.artist_phrase("get the mark day tones from gift of tone") == "mark day"
    assert artist_pack.artist_phrase("make the delay longer") is None
    assert artist_pack.artist_phrase("") is None


# --- resolve -----------------------------------------------------------------

def test_resolve_exact_first_name_and_band():
    r = artist_pack.resolve("devin townsend", FIXTURE)
    assert r.status == "resolved" and r.entry["id"] == "got-devin"
    r = artist_pack.resolve("devin", FIXTURE)          # first name, unique
    assert r.status == "resolved" and r.entry["id"] == "got-devin"
    r = artist_pack.resolve("periphery", FIXTURE)      # a band entry
    assert r.status == "resolved" and r.entry["id"] == "got-periphery"
    r = artist_pack.resolve("vai", FIXTURE)            # a surname alone
    assert r.status == "resolved" and r.entry["id"] == "got-vai"
    r = artist_pack.resolve("justin york", FIXTURE)    # full name beats the tie
    assert r.status == "resolved" and r.entry["id"] == "got-york"


def test_resolve_ambiguous_asks_one_question_naming_the_candidates():
    r = artist_pack.resolve("mark", FIXTURE)
    assert r.status == "ambiguous" and r.entry is None
    assert [c["id"] for c in r.candidates] == ["got-markday", "got-moke"]
    assert r.question == "Which Mark: Mark Day (2022) or Mark 'Moke' Perry (2022)?"
    r = artist_pack.resolve("justin", FIXTURE)
    assert r.status == "ambiguous"
    assert [c["id"] for c in r.candidates] == ["got-derrico", "got-york"]


def test_resolve_absent_is_honest():
    r = artist_pack.resolve("meshuggah", FIXTURE)
    assert r.status == "absent" and r.entry is None and r.candidates == []
    assert r.line == ("There is no official Gift of Tone pack for Meshuggah; "
                      "building a tone in that style from what I know, which "
                      "is my interpretation, not their preset.")
    assert artist_pack.resolve("", FIXTURE).status == "absent"


def test_resolve_on_the_shipped_catalog():
    doc = json.loads((ROOT / "catalog" / "gift_of_tone.json").read_text())
    entries = doc["entries"]
    assert artist_pack.resolve("devin townsend", entries).entry["id"] == "got-2022-14"
    assert artist_pack.resolve("devin", entries).entry["id"] == "got-2022-14"
    assert artist_pack.resolve("periphery", entries).entry["id"] == "got-2024-1"
    assert artist_pack.resolve("steve", entries).entry["id"] == "got-2022-16"
    mark = artist_pack.resolve("mark", entries)
    assert mark.status == "ambiguous" and len(mark.candidates) == 2
    assert artist_pack.resolve("meshuggah", entries).status == "absent"


# --- the route and the bar ---------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(gift_of_tone, "fetch",
                        lambda timeout=6.0: ({"entries": FIXTURE}, "fixture", None))
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    return TestClient(server.app)


def test_route_resolved_names_the_pack_and_fetches_nothing(client, monkeypatch):
    fetched = []
    monkeypatch.setattr(gallery, "fetch_entry",
                        lambda *a, **k: fetched.append(1))
    d = client.post("/api/artist-pack", json={"query": "sound like Devin Townsend"}).json()
    assert d["status"] == "resolved" and d["entry"]["id"] == "got-devin"
    assert d["confirm"] == ("Found the Devin Townsend (2022) pack on Gift of "
                            "Tone: preset plus cab bundle. Fetch it?")
    assert d["entry"]["min_firmware"] == "4.0"
    assert fetched == [] and server._install_cache == {}


def test_route_ambiguous_and_absent_shapes(client):
    d = client.post("/api/artist-pack", json={"query": "give me Mark's rig"}).json()
    assert d["status"] == "ambiguous"
    assert [c["id"] for c in d["candidates"]] == ["got-markday", "got-moke"]
    assert d["question"].startswith("Which Mark: ")
    d = client.post("/api/artist-pack", json={"query": "sound like Meshuggah"}).json()
    assert d["status"] == "absent"
    assert "no official Gift of Tone pack for Meshuggah" in d["line"]
    assert "interpretation" in d["line"]
    r = client.post("/api/artist-pack", json={"query": "make it brighter"})
    assert r.status_code == 400


def test_route_offers_only_what_this_unit_can_take(client):
    d = client.post("/api/artist-pack", json={"query": "sound like Axe Only"}).json()
    assert d["status"] == "absent"          # filtered out for an FM9


def test_the_bar_resolves_before_the_planner_and_fetches_on_a_click_only():
    assert "function artistIntent(said)" in UI
    assert "async function runArtistPack(said)" in UI
    # both entry points consult the resolver first
    assert "if (artistIntent(said) && await runArtistPack(said))" in UI
    # a resolved pack is an OFFER with a button; fetching waits for the click
    assert "role: 'offer'" in UI and 'class="small offer"' in UI
    assert "b.onclick = () => runAcquire(b.dataset.said)" in UI
    # the absent line is shown, then the sentence continues to the planner
    assert "chatNote(esc(d.line));        // shown first; the planner builds next" in UI


def test_rule_23_is_in_the_rulebook():
    assert "## 23. An artist's name without their official pack is an interpretation (issue #158)" in RULES
    assert "in the style of" in RULES and "never say" in RULES
