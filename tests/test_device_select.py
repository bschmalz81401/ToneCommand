"""Issue #94: with more than one device reachable, the tool determines or
asks which one a build targets before acting, on the #124 seam.

The HeadRush side is the simulator (TONECOMMAND_HEADRUSH_SIM=1) through the
committed client; no hardware."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    monkeypatch.setattr(server, "_plan_revisions", {})
    monkeypatch.setattr(server, "_selected_kind", {"kind": None})
    monkeypatch.setattr(server, "_context", server._default_context)
    monkeypatch.delenv("TONECOMMAND_HEADRUSH_HOST", raising=False)
    monkeypatch.delenv("TONECOMMAND_HEADRUSH_SIM", raising=False)
    return TestClient(server.app)


def test_one_device_is_the_target_without_being_asked(client):
    d = client.get("/api/device").json()
    assert d["selected"] == "fm9" and d["ambiguous"] is False
    assert [x["kind"] for x in d["available"]] == ["fm9"]


def test_two_devices_and_no_choice_is_ambiguous(client, monkeypatch):
    monkeypatch.setenv("TONECOMMAND_HEADRUSH_SIM", "1")
    d = client.get("/api/device").json()
    assert d["ambiguous"] is True and d["selected"] is None
    assert [x["kind"] for x in d["available"]] == ["fm9", "headrush"]
    assert d["active"] == "fm9", "nothing switched on its own"


def test_an_ambiguous_target_refuses_to_plan_on_both_routes(client, monkeypatch):
    monkeypatch.setenv("TONECOMMAND_HEADRUSH_SIM", "1")
    r = client.post("/api/plan", json={"prompt": "a bit more drive"})
    assert r.status_code == 409, r.text
    assert r.json()["ambiguous_device"] == ["fm9", "headrush"]
    assert "which device" in r.json()["error"]
    r = client.post("/api/plan/stream", json={"prompt": "a bit more drive"})
    assert r.status_code == 409


def test_selecting_the_headrush_switches_the_context_to_its_registry(client, monkeypatch):
    monkeypatch.setenv("TONECOMMAND_HEADRUSH_SIM", "1")
    r = client.post("/api/device/select", json={"kind": "headrush"})
    assert r.status_code == 200, r.text
    ctx = server.device_context()
    assert ctx.kind == "headrush" and type(ctx.adapter).__name__ == "HeadrushAdapter"
    assert type(ctx.registry).__name__ == "Registry" and ctx.registry is not server.FM9_REGISTRY
    d = client.get("/api/device").json()
    assert d["selected"] == "headrush" and d["active"] == "headrush" and not d["ambiguous"]
    # and back
    r = client.post("/api/device/select", json={"kind": "fm9"})
    assert r.status_code == 200 and server.device_context().kind == "fm9"


def test_select_is_refused_for_an_unknown_kind_under_gig_lock_and_with_a_pending_plan(client, monkeypatch):
    monkeypatch.setenv("TONECOMMAND_HEADRUSH_SIM", "1")
    assert client.post("/api/device/select", json={"kind": "tonex"}).status_code == 404
    monkeypatch.setattr(server, "_gig_mode", {"on": True})
    assert client.post("/api/device/select", json={"kind": "headrush"}).status_code == 423
    monkeypatch.setattr(server, "_gig_mode", {"on": False})
    monkeypatch.setattr(server, "_plan_revisions", {"abc": {"at": 0, "n": 1, "note": ""}})
    r = client.post("/api/device/select", json={"kind": "headrush"})
    assert r.status_code == 409 and "pending" in r.json()["error"]
    assert server.device_context().kind == "fm9"


def test_single_fm9_planning_is_unchanged(client, monkeypatch):
    """No HeadRush reachable: /api/plan never sees the ambiguity check fire."""
    calls = []
    monkeypatch.setattr(server, "_plan_for", lambda body, **kw: (calls.append(body.prompt), {"summary": "x", "actions": []})[1])
    r = client.post("/api/plan", json={"prompt": "hello"})
    assert r.status_code == 200 and calls == ["hello"]
