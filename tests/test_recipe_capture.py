"""Issue #153 (I10): a recipe that uses a capture carries it by reference,
never the file. Serialisation, the refusals, the four recipient outcomes
with a fake fetch, and the two routes. No network (the fetch is a fake
and the conftest guard stands); the server writes no capture bytes.
"""
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server
from fm9 import recipe_capture as rc, recipes, share
from fm9.sim import SimFM9
from tests.test_nam_intake_set import _nam

ROOT = Path(__file__).resolve().parent.parent
UI = (ROOT / "ui" / "index.html").read_text()

NAM = _nam("Mesa Boogie Mark V", 1)
SHA = hashlib.sha256(NAM).hexdigest()
URL = "https://www.tone3000.com/tones/mesa-boogie-mark-v-57410"
AMP = next(iter(server.reg.amp_roster.values()))


def _field(**over):
    f = rc.serialise(NAM, 57410, 353891, URL, "t3k", AMP)
    f.update(over)
    return f


def _recipe(**over):
    r = {"recipe_version": 1, "name": "mark-v-by-capture", "title": "Mark V, the capture",
         "device": "FM9", "author": "monzta1", "tested_firmware": "11.00",
         "actions": [{"kind": "set_type", "block": "amp", "type_name": AMP,
                      "reason": "the amp the capture stands for"}],
         "capture": _field()}
    r.update(over)
    return r


# --- REQ-001: serialise, validate, refuse ----------------------------------------------------

def test_serialise_carries_the_source_ids_hash_and_stand_in_only():
    f = rc.serialise(NAM, 57410, 353891, URL, "t3k", AMP)
    assert f == {"source": "TONE3000", "tone_id": 57410, "model_id": 353891, "url": URL,
                 "license": "t3k", "sha256": SHA, "stands_for": {"block": "amp", "type_name": AMP}}
    blob = json.dumps(f)
    assert base64.b64encode(NAM).decode()[:40] not in blob
    assert NAM[:16].decode("latin-1") not in blob
    assert len(blob) < rc.MAX_FIELD_BYTES
    assert rc.validate_field(f) is None and rc.validate(_recipe(), {AMP}) is None
    assert rc.validate({"name": "no capture here"}) is None


def test_validate_refuses_five_ways_in_one_line_each():
    assert "only TONE3000" in rc.validate_field(_field(source="ToneHunt"))
    assert "64 hex" in rc.validate_field(_field(sha256="abc"))
    assert "file data" in rc.validate_field(_field(url="data:application/octet-stream;base64,AAAA"))
    assert "file data" in rc.validate_field({**_field(), "license": base64.b64encode(NAM * 20).decode()[:600]})
    assert "not an amp model" in rc.validate_field(_field(stands_for={"block": "amp", "type_name": "Nope"}), {AMP})
    assert "this format does not have" in rc.validate_field({**_field(), "file": "x"})
    assert "is missing" in rc.validate_field({k: v for k, v in _field().items() if k != "model_id"})
    assert "not a TONE3000 page" in rc.validate_field(_field(url="https://example.com/x"))
    assert "not a TONE3000 id" in rc.validate_field(_field(tone_id="57410"))
    assert "not a record" in rc.validate_field("57410")
    with pytest.raises(rc.RecipeCaptureError, match="no capture file"):
        rc.serialise(b"", 1, 1, URL, "t3k", AMP)


# --- REQ-002: the recipient's four outcomes ----------------------------------------------------

def _fetch(record_status=200, file_status=200, data=NAM, model_url=None, raise_on=None):
    calls = []
    url = model_url or "https://www.tone3000.com/api/v1/models/353891/download/abc.nam"

    def fetch(u, key):
        calls.append((u, key))
        if raise_on and raise_on in u:
            raise OSError("boom")
        if u.endswith("/models/353891"):
            return record_status, json.dumps({"id": 353891, "model_url": url}).encode()
        if u == url:
            return file_status, data
        return 404, b""
    fetch.calls = calls
    return fetch


def test_resolve_available_from_the_library_fetches_nothing():
    f = _fetch()
    out = rc.resolve(_field(), {SHA}, fetch=f, key="t3k_cs_x")
    assert out["status"] == "available" and "in your library" in out["line"]
    assert out["bytes"] is None and f.calls == []


def test_resolve_available_by_fetch_under_the_recipients_own_key_and_hash():
    f = _fetch()
    out = rc.resolve(_field(), set(), fetch=f, key="t3k_cs_mine")
    assert out["status"] == "available" and out["bytes"] == NAM
    assert "under your own TONE3000 account" in out["line"]
    assert [k for _u, k in f.calls] == ["t3k_cs_mine", "t3k_cs_mine"]
    assert f.calls[0][0] == "https://www.tone3000.com/api/v1/models/353891"


def test_resolve_not_yours_without_a_key_on_401_403_or_a_private_tone():
    f = _fetch()
    out = rc.resolve(_field(), set(), fetch=f, key=None)
    assert out["status"] == "not_yours" and f.calls == []
    assert out["link"] == URL and f"built with {AMP}" in out["line"]
    for code in (401, 403):
        out = rc.resolve(_field(), set(), fetch=_fetch(record_status=code), key="k")
        assert out["status"] == "not_yours" and "did not allow your account" in out["line"]
        out = rc.resolve(_field(), set(), fetch=_fetch(file_status=code), key="k")
        assert out["status"] == "not_yours" and out["bytes"] is None
    out = rc.resolve(_field(), set(), fetch=f, key="k", is_public=False)
    assert out["status"] == "not_yours" and "not public" in out["line"] and f.calls == []


def test_resolve_missing_on_404_or_a_different_file_and_unreachable_otherwise():
    out = rc.resolve(_field(), set(), fetch=_fetch(record_status=404), key="k")
    assert out["status"] == "missing" and "gone from TONE3000" in out["line"]
    out = rc.resolve(_field(), set(), fetch=_fetch(data=NAM + b"x"), key="k")
    assert out["status"] == "missing" and "different file" in out["line"] and out["bytes"] is None
    for f in (_fetch(record_status=500), _fetch(file_status=503), _fetch(record_status=0),
              _fetch(raise_on="/models/"), _fetch(data=b""),
              _fetch(model_url="https://evil.example/x.nam"), _fetch(data=b"x" * (rc.MAX_BYTES + 1))):
        out = rc.resolve(_field(), set(), fetch=f, key="k")
        assert out["status"] == "unreachable", f.calls
        assert f"built with {AMP} for now" in out["line"] and out["bytes"] is None
    assert set(rc.STATUSES) == {"available", "not_yours", "missing", "unreachable"}


def test_default_fetch_never_leaves_tone3000():
    assert rc.default_fetch("https://example.com/a.nam", "k") == (0, b"")


# --- REQ-003: the routes and the page ---------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_fm9", SimFM9(server.reg))
    monkeypatch.setenv("TONECOMMAND_RECIPES_DIR", str(tmp_path / "recipes"))
    monkeypatch.setenv("TONECOMMAND_OUTBOX", str(tmp_path / "outbox.json"))
    monkeypatch.delenv("TONE3000_SECRET_KEY", raising=False)
    monkeypatch.setattr(rc, "_ENV_FILE", tmp_path / "no.env")
    monkeypatch.setattr(share, "sync", lambda timeout=6.0: {"sent": 0})
    return TestClient(server.app)


def _files_under(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


def test_plan_route_resolves_the_capture_and_keeps_the_actions(client, monkeypatch, tmp_path):
    r = client.post("/api/recipes/plan", json={"recipe": _recipe(), "known": [SHA]}).json()
    assert r["capture"]["status"] == "available" and "in your library" in r["capture"]["line"]
    assert [a["kind"] for a in r["actions"]] == ["set_type"]
    r = client.post("/api/recipes/plan", json={"recipe": _recipe()}).json()
    assert r["capture"]["status"] == "not_yours" and r["capture"]["link"] == URL
    assert f"built with {AMP}" in r["capture"]["line"]
    # fetched under the recipient's key: through intake in memory, no file anywhere
    monkeypatch.setenv("TONE3000_SECRET_KEY", "t3k_cs_mine")
    monkeypatch.setattr(rc, "default_fetch", _fetch())
    before = _files_under(tmp_path)
    home_before = _files_under(Path.home() / ".tonecommand") if (Path.home() / ".tonecommand").exists() else set()
    r = client.post("/api/recipes/plan", json={"recipe": _recipe()}).json()
    assert r["capture"]["status"] == "available" and "bytes" not in r["capture"]
    assert r["capture"]["intake"]["items"][0]["sha256"] == SHA
    assert _files_under(tmp_path) == before
    home_after = _files_under(Path.home() / ".tonecommand") if (Path.home() / ".tonecommand").exists() else set()
    assert home_after == home_before
    # an invalid capture field is said, the actions still planned
    bad = _recipe(capture=_field(source="ToneHunt"))
    r = client.post("/api/recipes/plan", json={"recipe": bad}).json()
    assert r["capture"]["status"] == "invalid" and "only TONE3000" in r["capture"]["line"]
    assert len(r["actions"]) == 1
    # a recipe without a capture is untouched
    r = client.post("/api/recipes/plan", json={"recipe": _recipe(capture=None)}).json()
    assert r["capture"]["status"] == "invalid"
    plain = {k: v for k, v in _recipe().items() if k != "capture"}
    r = client.post("/api/recipes/plan", json={"recipe": plain}).json()
    assert "capture" not in r


def test_save_route_refuses_a_smuggled_file_before_saving_or_queueing(client, tmp_path):
    bad = _recipe(capture={**_field(), "url": "data:application/octet-stream;base64," + base64.b64encode(NAM).decode()})
    r = client.post("/api/recipes/save", json={"recipe": bad})
    assert r.status_code == 400 and "file data" in r.json()["error"]
    assert not (tmp_path / "recipes").exists() or not list((tmp_path / "recipes").glob("*.json"))
    assert share.pending() == []
    r = client.post("/api/recipes/save", json={"recipe": _recipe()})
    assert r.status_code == 200, r.text
    saved = json.loads(next((tmp_path / "recipes").glob("*.json")).read_text())
    assert saved["capture"]["sha256"] == SHA and "bytes" not in json.dumps(saved)


def test_the_page_says_a_recipe_uses_a_capture_and_logs_the_line():
    assert "Uses a capture from ${esc(r.capture.source)}" in UI
    assert "d.capture.line" in UI
    assert "known: namLibraryKnown()" in UI
    src = (ROOT / "server.py").read_text()
    assert "known: list[str] = []" in src
