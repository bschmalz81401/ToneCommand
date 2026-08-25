"""Choosing the planner backend from the UI (issue #24).

The rules that most need tests are the key-handling ones: a key must never
reach the browser, a blank key must keep the stored one, and clearing takes
an explicit flag. Getting any of those wrong leaks a secret into a page or
silently discards one.
"""
import json

import pytest
from fastapi.testclient import TestClient

from fm9 import ai_settings, planner


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the settings file at a tmp path, and start from a clean env."""
    path = tmp_path / "ai_settings.json"
    monkeypatch.setenv("TONECOMMAND_AI_SETTINGS", str(path))
    for name in ("PLANNER_BACKEND", "PLANNER_BASE_URL", "PLANNER_MODEL",
                 "PLANNER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return path


@pytest.fixture
def client(store, monkeypatch):
    import server
    monkeypatch.setattr(server, "_fm9", None, raising=False)
    return TestClient(server.app)


# --- the key never travels ---

def test_the_public_projection_reports_only_that_a_key_exists(store):
    saved = ai_settings.save({"backend": "openai", "baseUrl": "http://h/v1",
                              "apiKey": "sk-secret"})
    pub = saved.public()
    assert pub["hasKey"] is True
    assert "sk-secret" not in json.dumps(pub)
    assert "apiKey" not in pub and "api_key" not in pub


def test_the_endpoint_never_returns_the_key(client):
    client.post("/api/ai-settings", json={"backend": "openai",
                                          "baseUrl": "http://h/v1",
                                          "apiKey": "sk-must-not-leak"})
    got = client.get("/api/ai-settings")
    assert "sk-must-not-leak" not in got.text
    assert got.json()["settings"]["hasKey"] is True


def test_a_blank_key_keeps_the_stored_one(store):
    ai_settings.save({"backend": "openai", "apiKey": "sk-keep"})
    assert ai_settings.save({"backend": "openai", "baseUrl": "http://new/v1"}) \
        .key_for() == "sk-keep"
    assert ai_settings.save({"backend": "openai", "apiKey": ""}).key_for() == "sk-keep"


def test_clearing_takes_an_explicit_flag(store):
    ai_settings.save({"backend": "openai", "apiKey": "sk-drop"})
    assert ai_settings.save({"backend": "openai", "clearKey": True}).key_for() == ""
    assert ai_settings.load().key_for() == ""


def test_the_stored_file_is_gitignored():
    from pathlib import Path
    ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text()
    assert "ai_settings.json" in ignore, "the settings file holds an API key"


# --- precedence and persistence ---

def test_the_file_wins_over_the_environment(store, monkeypatch):
    monkeypatch.setenv("PLANNER_BACKEND", "cli")
    assert ai_settings.load().backend == "cli"
    ai_settings.save({"backend": "grok"})
    assert ai_settings.load().backend == "grok"


def test_the_environment_is_the_fallback_when_no_file_exists(store, monkeypatch):
    monkeypatch.setenv("PLANNER_BASE_URL", "http://from-env/v1")
    assert not store.exists()
    assert ai_settings.load().base_url == "http://from-env/v1"


def test_a_choice_survives_a_restart(store):
    ai_settings.save({"backend": "grok", "model": "grok-4.6-build"})
    reloaded = ai_settings.load()           # a fresh read, as a new process would
    assert (reloaded.backend, reloaded.model_for()) == ("grok", "grok-4.6-build")


def test_a_corrupt_file_does_not_break_startup(store, monkeypatch):
    store.write_text("{ this is not json")
    monkeypatch.setenv("PLANNER_BACKEND", "cli")
    assert ai_settings.load().backend == "cli"


# --- the choice reaches the planner without changing it ---

def test_saving_makes_the_choice_effective_for_the_next_prompt(store):
    ai_settings.save({"backend": "grok"})
    assert planner.candidates() == ["grok"], \
        "the planner reads its own configuration; saving must land there"


def test_apply_to_env_clears_what_is_no_longer_set(store, monkeypatch):
    ai_settings.save({"backend": "openai", "baseUrl": "http://h/v1"})
    assert planner._openai_base_url() == "http://h/v1"
    ai_settings.save({"backend": "cli"})
    assert planner._openai_base_url() == "", \
        "a base URL must not steer a backend that never reads it"


def test_an_unknown_backend_is_refused(store, client):
    with pytest.raises(ValueError, match="unknown backend"):
        ai_settings.save({"backend": "gpt9"})
    assert client.post("/api/ai-settings", json={"backend": "gpt9"}).status_code == 400


# --- only offer what the host can run ---

def test_unavailable_backends_are_reported_with_a_reason(store, monkeypatch):
    monkeypatch.setattr(planner, "find_grok_cli", lambda: None)
    monkeypatch.setattr(planner, "find_claude_cli", lambda: None)
    by_name = {b["backend"]: b for b in ai_settings.available_backends()}
    assert by_name["grok"]["available"] is False
    assert "grok binary" in by_name["grok"]["why"]
    assert by_name["cli"]["available"] is False


def test_availability_lists_auto_first_then_the_planner_order(store):
    """Auto is what a fresh install does, so it heads the list and is always
    available; the rest follow the planner's own candidate order."""
    order = [b["backend"] for b in ai_settings.available_backends()]
    assert order == [""] + list(planner.BACKENDS)
    assert ai_settings.available_backends()[0]["available"] is True


def test_a_configured_base_url_makes_the_openai_choice_available(store):
    def openai_entry():
        return [b for b in ai_settings.available_backends()
                if b["backend"] == "openai"][0]
    assert openai_entry()["available"] is False
    ai_settings.save({"backend": "", "baseUrl": "http://127.0.0.1:8317/v1"})
    assert openai_entry()["available"] is True


# --- the UI surfaces which backend answered ---

def test_the_ui_shows_the_answering_backend():
    from pathlib import Path
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    assert "planned by" in ui, "a plan must be attributable to the model behind it"
    assert "plan.backend" in ui and "plan.model" in ui


def test_no_em_dashes_in_the_ui_or_the_settings_module():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    for rel in ("ui/index.html", "fm9/ai_settings.py"):
        assert "—" not in (root / rel).read_text(), f"em dash in {rel}"


# --- every control must map to a variable the chosen backend actually reads ---

def test_each_backend_declares_only_the_controls_it_honours(store):
    by_name = {b["backend"]: b for b in ai_settings.available_backends()}
    # the Claude CLI reads nothing: its model is a constant in the planner
    assert (by_name["cli"]["needsBaseUrl"], by_name["cli"]["needsModel"],
            by_name["cli"]["needsKey"]) == (False, False, False)
    # the Claude API needs a key; its model is a constant too
    assert by_name["api"]["needsKey"] and not by_name["api"]["needsModel"]
    # the Grok CLI takes a model but no key
    assert by_name["grok"]["needsModel"] and not by_name["grok"]["needsKey"]
    assert by_name["openai"]["needsBaseUrl"] and by_name["openai"]["needsModel"]


def test_the_grok_model_lands_in_the_variable_grok_reads(store):
    """It reads GROK_CLI_MODEL, not PLANNER_MODEL, so a shared box would have
    done nothing at all."""
    import os
    ai_settings.save({"backend": "grok", "model": "grok-4.6-build"})
    assert os.environ.get("GROK_CLI_MODEL") == "grok-4.6-build"
    assert "PLANNER_MODEL" not in os.environ


def test_the_claude_api_key_lands_in_anthropic_api_key(store):
    """The Claude API path reads ANTHROPIC_API_KEY. Storing it as
    PLANNER_API_KEY left that backend permanently unselectable."""
    import os
    ai_settings.save({"backend": "api", "apiKey": "sk-ant-real"})
    assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-real"
    assert "PLANNER_API_KEY" not in os.environ
    assert planner._api_available() is True
    assert [b for b in ai_settings.available_backends()
            if b["backend"] == "api"][0]["available"] is True


def test_keys_are_stored_per_backend_not_shared(store):
    """An OpenAI router key must never quietly become an Anthropic one."""
    ai_settings.save({"backend": "openai", "baseUrl": "http://h/v1",
                      "apiKey": "sk-router"})
    ai_settings.save({"backend": "api", "apiKey": "sk-ant"})
    stored = ai_settings.load()
    assert stored.keys["openai"] == "sk-router"
    assert stored.keys["api"] == "sk-ant"


def test_a_stale_value_cannot_steer_a_backend_that_ignores_it(store):
    import os
    ai_settings.save({"backend": "openai", "baseUrl": "http://h/v1",
                      "model": "llama3.3"})
    ai_settings.save({"backend": "grok"})
    assert "PLANNER_BASE_URL" not in os.environ
    assert "PLANNER_MODEL" not in os.environ


def test_auto_mode_still_honours_a_configured_endpoint(store):
    """In auto the planner tries a configured router first (#21), so a base
    URL is meaningful there even with no backend pinned."""
    import os
    ai_settings.save({"backend": "", "baseUrl": "http://127.0.0.1:8317/v1"})
    assert os.environ.get("PLANNER_BASE_URL") == "http://127.0.0.1:8317/v1"
    assert planner.candidates()[0] == "openai"
