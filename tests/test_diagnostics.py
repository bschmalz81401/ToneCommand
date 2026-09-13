"""Epic H (#106): #107 local secret-scrubbed logging, #108 voluntary share.
"""
from __future__ import annotations

import inspect
import json
import time

from fm9 import diagnostics as diag


# --- #107: scrubbing -------------------------------------------------------

def test_scrub_redacts_known_secret_patterns():
    assert diag.REDACTED in diag.scrub_text("key is sk-ant-api03-abcDEF12345xyz")
    assert diag.REDACTED in diag.scrub_text("token sk-abcdefghijklmnopqrstuvwx")
    assert diag.REDACTED in diag.scrub_text("Authorization: Bearer abc123.def456")
    scrubbed = diag.scrub_text("ANTHROPIC_API_KEY=sk-ant-verysecretvalue123")
    assert "sk-ant-verysecretvalue123" not in scrubbed
    assert "ANTHROPIC_API_KEY" in scrubbed, "the key NAME is fine to keep, only the value is secret"


def test_scrub_leaves_ordinary_text_alone():
    text = "the lead scene gain 7.8 was below the rhythm's 6.8"
    assert diag.scrub_text(text) == text


def test_scrub_recurses_through_dicts_and_lists():
    obj = {"msg": "using PLANNER_API_KEY=sk-abcdefghijklmnopqrstuvwx now",
           "nested": {"a": ["fine", "Bearer supersecrettoken123"]}}
    out = diag.scrub(obj)
    assert diag.REDACTED in out["msg"]
    assert diag.REDACTED in out["nested"]["a"][1]
    assert out["nested"]["a"][0] == "fine"


# --- #107: local structured log, scoped and expiring -----------------------

def test_local_log_is_structured_scrubbed_and_expires(tmp_path):
    path = tmp_path / "diag.jsonl"
    diag.log_error("planner", "backend failed with sk-ant-api03-realsecret999",
                    path=path, backend="cli")
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["scope"] == "planner"
    assert "realsecret999" not in entry["message"]
    assert entry["context"]["backend"] == "cli"
    assert "ts" in entry

    # an old entry is pruned on the next write
    old = {"ts": time.time() - (diag.RETENTION_DAYS + 1) * 86400,
           "scope": "old", "message": "stale", "context": {}}
    with path.open("a") as fh:
        fh.write(json.dumps(old) + "\n")
    diag.log_error("planner", "a second, fresher error", path=path)
    remaining = [json.loads(l) for l in path.read_text().splitlines()]
    assert all(e["scope"] != "old" for e in remaining), "expired entry must be pruned"
    assert any(e["message"] == "a second, fresher error" for e in remaining)


def test_prune_expired_is_safe_on_a_missing_file(tmp_path):
    assert diag.prune_expired(path=tmp_path / "nope.jsonl") == 0


def test_read_recent_filters_by_scope_and_orders_newest_first(tmp_path):
    path = tmp_path / "diag.jsonl"
    diag.log_error("planner", "first", path=path)
    diag.log_error("server", "second", path=path)
    diag.log_error("planner", "third", path=path)
    recent = diag.read_recent(scope="planner", path=path)
    assert [e["message"] for e in recent] == ["third", "first"]


def test_diagnostics_module_makes_no_network_calls():
    """Issue #107's AC: nothing is uploaded to any server by default. The
    whole module must be structurally incapable of a network call, not just
    unconfigured to make one."""
    src = inspect.getsource(diag)
    for forbidden in ("requests", "httpx", "urllib.request", "socket",
                       "http.client", "aiohttp", "fetch("):
        assert forbidden not in src, forbidden


# --- #108: voluntary share package ------------------------------------------

def test_share_package_is_scrubbed_and_never_sent_automatically(tmp_path):
    path = tmp_path / "diag.jsonl"
    diag.log_error("planner", "leaked sk-ant-api03-shouldnotship1234", path=path)
    pkg = diag.package_for_sharing(path=path)
    assert "shouldnotship1234" not in pkg["body"]
    assert "shouldnotship1234" not in pkg["title"]
    assert pkg["url"].startswith("https://github.com/monzta1/ToneCommand/issues/new")
    assert pkg["entry_count"] == 1

    # package_for_sharing must not itself perform any network I/O - it
    # returns text and a URL for the player to open, nothing more.
    src = inspect.getsource(diag.package_for_sharing)
    for forbidden in ("requests", "httpx", "urlopen", "socket", ".post(", "urlretrieve"):
        assert forbidden not in src, forbidden


def test_share_package_with_no_entries_is_still_reviewable(tmp_path):
    pkg = diag.package_for_sharing(path=tmp_path / "empty.jsonl")
    assert pkg["entry_count"] == 0
    assert "no local diagnostics" in pkg["body"].lower()


def test_share_package_can_be_scoped_to_one_subsystem(tmp_path):
    path = tmp_path / "diag.jsonl"
    diag.log_error("planner", "planner issue", path=path)
    diag.log_error("server", "server issue", path=path)
    pkg = diag.package_for_sharing(scope="planner", path=path)
    assert pkg["entry_count"] == 1
    assert "planner issue" in pkg["body"]
    assert "server issue" not in pkg["body"]
