"""Issue #159 (J6): installed artist packs as a reference. The record of
every gallery install, compare resolving an artist name to the pack on
the unit (snapshot, select, capture, return, restore), and the tagged
evidence the planner reads. Everything on the simulator; no network.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import server
from fm9 import advisory, editbuffer, installed_packs as ip
from fm9.sim import SimFM9

from tests.test_gallery_install import client, sim  # noqa: F401  the fixtures

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("TONECOMMAND_INSTALLED_PACKS", str(tmp_path / "packs.json"))
    yield tmp_path / "packs.json"


DEVIN = {"id": "got-2022-14", "artists": ["Devin Townsend"], "year": 2022, "number": "14"}
LUKE = {"id": "got-luke", "artists": ["Steve Lukather"], "year": 2025, "number": "1"}
LUKE_OLD = {"id": "got-luke-2023", "artists": ["Steve Lukather"], "year": 2023, "number": "3"}
RESULT = {"preset": "BT Devin 01", "store_slot": 134,
          "cabs": [{"slot": 515, "name": "BT_Dev_Cab"}]}


def _ev(artist="Devin Townsend", year=2022, **over):
    rec = {"tag": {"artist": artist, "year": year, "source": ip.SOURCE},
           "kind": "amp", "name": "FAS Modern", "engaged": True,
           "settings": {"gain": 7.5, "bass": 5.0, "mid": 5.0, "treble": 6.0,
                        "master": 4.0, "presence": None}}
    rec.update(over)
    return rec


# --- REQ-001: the record, find, listing -----------------------------------------------

def test_record_writes_one_file_and_listing_reads_it_newest_first(store):
    ip.record(LUKE_OLD, dict(RESULT, preset="BT Luke 03", store_slot=140))
    ip.record(DEVIN, RESULT, [_ev()])
    doc = json.loads(store.read_text())
    assert doc["version"] == 1 and [r["id"] for r in doc["packs"]] == ["got-luke-2023", "got-2022-14"]
    rec = doc["packs"][1]
    assert rec["artists"] == ["Devin Townsend"] and rec["year"] == 2022
    assert rec["label"] == "Devin Townsend (2022)" and rec["preset_name"] == "BT Devin 01"
    assert rec["slot"] == 134 and rec["cabs"] == [{"slot": 515, "name": "BT_Dev_Cab"}]
    assert rec["installed_at"].endswith("Z") and rec["evidence"][0]["kind"] == "amp"
    assert [r["id"] for r in ip.listing()][0] in ("got-2022-14", "got-luke-2023")
    # a newer install of the same entry replaces the older
    ip.record(DEVIN, dict(RESULT, store_slot=135))
    assert [r["slot"] for r in ip.listing() if r["id"] == "got-2022-14"] == [135]


def test_record_refuses_a_bad_id_and_untagged_evidence(store):
    with pytest.raises(ip.InstalledPacksError, match="not a catalog id"):
        ip.record({"id": "Not An Id!"}, RESULT)
    with pytest.raises(ip.InstalledPacksError, match="no artist"):
        ip.record(DEVIN, RESULT, [_ev(artist="")])
    with pytest.raises(ip.InstalledPacksError, match="source"):
        ip.record(DEVIN, RESULT, [{**_ev(), "tag": {"artist": "D", "year": 2022, "source": "interview"}}])
    assert not store.exists()


def test_find_resolves_with_the_artist_pack_rules(store):
    assert ip.find("devin").status == "absent"
    assert "no artist pack is installed" in ip.find("devin").line
    ip.record(DEVIN, RESULT)
    ip.record(LUKE_OLD, dict(RESULT, preset="BT Luke 03", store_slot=140))
    ip.record(LUKE, dict(RESULT, preset="BT Luke 01", store_slot=139))
    assert ip.find("devin townsend").entry["id"] == "got-2022-14"
    assert ip.find("devin").entry["id"] == "got-2022-14"          # whole word
    assert ip.find("own").status == "absent"                       # never a substring
    assert "installed:" in ip.find("own").line
    assert ip.find("lukather").entry["id"] == "got-luke"          # newest year wins
    two = ip.record({"id": "got-vai", "artists": ["Steve Vai"], "year": 2024}, RESULT)
    res = ip.find("steve")
    assert res.status == "ambiguous" and res.question.startswith("Which Steve:")
    assert two["id"] == "got-vai"


def test_artist_words_strip_possessives_and_pack_words():
    assert ip.artist_words("Devin's") == "devin"
    assert ip.artist_words("Devin’s pack") == "devin"
    assert ip.artist_words("the Periphery rig") == "periphery"
    assert ip.artist_words("Misha Mansoor tones") == "misha mansoor"
    assert ip.artist_words("scene 1") == "scene 1"


# --- REQ-002: compare reads the pack off the unit and comes back ----------------------

def _install(client):
    r = client.post("/api/gift-of-tone/install", json={"id": "got-luke"})
    assert r.status_code == 200, r.text
    return r.json()


def _gain(sim):
    return advisory.value(server.reg, editbuffer.capture(sim, server.reg), "DISTORT", advisory.AMP["gain"])


def test_compare_source_resolves_an_installed_pack_and_restores_the_buffer(client, sim, monkeypatch):
    d = _install(client)
    assert d["installed_pack"] == {"id": "got-luke", "label": "Steve Lukather (2025)",
                                   "evidence": d["installed_pack"]["evidence"]}
    assert d["installed_pack"]["evidence"] >= 1
    # back on the player's own preset with an UNSAVED edit in the buffer
    sim.select_preset(3)
    spec = server.reg.spec("DISTORT", advisory.AMP["gain"], 1)
    sim.set_param_ordinal(spec, 0) if spec.dmin is None else sim.set_param_wire(spec, 60000)
    before = editbuffer.capture(sim, server.reg)
    gain_before = _gain(sim)
    for b in ("Steve Lukather's", "pack:Lukather", "Lukather"):
        r = client.post("/api/advise/compare", json={"a": "snapshot:a", "b": b})
        assert r.status_code in (200, 404), r.text
    server._snaps["a"] = before
    r = client.post("/api/advise/compare", json={"a": "snapshot:a", "b": "Steve Lukather's"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["a"] == "snapshot a" and out["b"] == "Steve Lukather (2025) pack"
    assert "not restored" not in out["b"]
    # the unit is back on preset 3 with the unsaved edit in place
    assert sim.current_preset()[0] == 3
    assert _gain(sim) == gain_before
    after = editbuffer.capture(sim, server.reg)
    assert editbuffer.diff(server.reg, before, after)["params"] == []
    assert sim.calls.count("store_preset") == 1            # the install's, none since


def test_compare_source_refuses_gig_lock_a_stale_slot_and_an_unknown_name(client, sim, monkeypatch):
    _install(client)
    sim.select_preset(3)
    server._snaps["a"] = editbuffer.capture(sim, server.reg)
    monkeypatch.setattr(server, "_gig_mode", {"on": True})
    r = client.post("/api/advise/compare", json={"a": "snapshot:a", "b": "Lukather"})
    assert r.status_code == 404 and "GIG LOCK" in r.json()["error"]
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    # somebody stored something else over the pack's slot since
    sim.select_preset(139)
    sim.rename_preset("Something Else")
    sim.store_preset(139)
    sim.select_preset(3)
    r = client.post("/api/advise/compare", json={"a": "snapshot:a", "b": "Lukather"})
    assert r.status_code == 404
    assert "holds 'Something Else' now" in r.json()["error"] and "install the pack again" in r.json()["error"]
    assert sim.current_preset()[0] == 3                       # came back regardless
    r = client.post("/api/advise/compare", json={"a": "snapshot:a", "b": "Nobody Here"})
    assert r.status_code == 404 and "installed: Steve Lukather (2025)" in r.json()["error"]
    r = client.post("/api/advise/compare", json={"a": "snapshot:a", "b": "scene 9"})
    assert r.status_code == 404 and "installed artist pack" in r.json()["error"]


def test_compare_source_refuses_before_any_select_when_the_unit_names_no_preset(client, sim, monkeypatch):
    _install(client)
    sim.select_preset(3)
    server._snaps["a"] = editbuffer.capture(sim, server.reg)
    monkeypatch.setattr(sim, "current_preset", lambda: None)
    n = sim.calls.count("select_preset")
    r = client.post("/api/advise/compare", json={"a": "snapshot:a", "b": "Lukather"})
    assert r.status_code == 404
    assert "nowhere to come back to" in r.json()["error"] and "refusing to select" in r.json()["error"]
    assert sim.calls.count("select_preset") == n                  # nothing selected


def test_compare_source_names_a_block_placed_unsaved_that_did_not_come_back(client, sim):
    _install(client)
    sim.select_preset(3)
    server._snaps["a"] = editbuffer.capture(sim, server.reg)
    n = len(server._snaps["a"]["blocks"])
    _fam, eid = server.reg.resolve_block("wah", 1)
    sim.place_block((1, 14), eid)                       # unsaved topology edit
    assert len(editbuffer.capture(sim, server.reg)["blocks"]) == n + 1
    r = client.post("/api/advise/compare", json={"a": "snapshot:a", "b": "Lukather"})
    assert r.status_code == 200, r.text
    assert r.json()["b"] == ("Steve Lukather (2025) pack (your buffer: not restored: "
                             "WAH 1 was placed unsaved and is gone; place it again)")
    assert sim.current_preset()[0] == 3
    assert len(editbuffer.capture(sim, server.reg)["blocks"]) == n


# --- REQ-003: evidence and the planner's reference ---------------------------------------

def test_evidence_from_the_pack_fixture_is_tagged_and_the_planner_accepts_it(client, sim, store):
    _install(client)
    rec = ip.listing()[0]
    ev = rec["evidence"]
    kinds = {e["kind"] for e in ev}
    assert "amp" in kinds and "cab" in kinds
    for e in ev:
        assert ip.validate_evidence(e) is None
        assert e["tag"] == {"artist": "Steve Lukather", "year": 2025, "source": "pack file"}
    amp = next(e for e in ev if e["kind"] == "amp")
    assert set(amp["settings"]) == set(ip.AMP_KNOBS)               # all six, always
    assert all(isinstance(v, float) for v in amp["settings"].values())
    cab = next(e for e in ev if e["kind"] == "cab")
    assert set(cab["settings"]) == {"bank", "type"} and isinstance(cab["settings"]["type"], int)
    for e in ev:
        assert isinstance(e["engaged"], bool) and set(e["settings"]) == ip.SETTINGS_KEYS[e["kind"]]
    blob = json.dumps(ev)
    assert "says" not in blob and '"' + "quote" not in blob


def test_validate_evidence_refuses_a_record_missing_its_tag():
    assert ip.validate_evidence(_ev()) is None
    assert ip.validate_evidence({**_ev(), "tag": None}) == "evidence has no tag"
    assert ip.validate_evidence(_ev(year=1999)) == "evidence tag has no year"
    assert ip.validate_evidence(_ev(kind="interview")).startswith("evidence kind")
    assert ip.validate_evidence(_ev(settings={**_ev()["settings"], "gain": "hot"})) == "evidence settings must be numbers or null"
    assert ip.validate_evidence(_ev(settings={"gain": 7.5})).startswith("evidence settings for amp must be exactly: bass, gain")
    assert ip.validate_evidence(_ev(kind="cab", settings={"bank": 2, "type": 515})) is None
    assert ip.validate_evidence(_ev(kind="cab", settings={})).startswith("evidence settings for cab")
    assert ip.validate_evidence(_ev(kind="drive", settings={"gain": 1})).endswith("must be exactly: nothing")
    assert ip.validate_evidence(_ev(engaged="yes")) == "evidence has no engaged flag"
    assert ip.validate_evidence("amp") == "evidence is not a record"


def test_reference_text_gains_the_installed_packs_section(client, sim, store):
    assert ip.reference_lines() == []
    _install(client)
    lines = ip.reference_lines()
    assert lines[0].startswith("\nINSTALLED ARTIST PACKS")
    assert "not the artist's words" in lines[0]
    assert lines[1].startswith("- Steve Lukather (2025): preset 'BT Luke 01' in slot 140; amp ")
    assert "[source: pack file, Steve Lukather (2025)]" in lines[1]
    # the section is read at request time, not baked into the cached reference
    assert "INSTALLED ARTIST PACKS" not in server.PARAM_REFERENCE
    src = (ROOT / "server.py").read_text()
    assert 'ref += "\\n".join(installed_packs.reference_lines())' in src
