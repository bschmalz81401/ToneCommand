"""Choosing the planner backend from the UI (issue #24).

The rules that most need tests are the key-handling ones: a key must never
reach the browser, a blank key must keep the stored one, and clearing takes
an explicit flag. Getting any of those wrong leaks a secret into a page or
silently discards one.
"""
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

from fm9 import ai_settings, planner


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the settings file at a tmp path, and start from a clean env.

    apply_to_env() writes real environment variables, which is the whole
    point of it, so the managed names are snapshotted and put back. monkeypatch
    only restores what monkeypatch itself set, and a variable a test caused
    the module to set would otherwise follow it into the next test.
    """
    path = tmp_path / "ai_settings.json"
    monkeypatch.setenv("TONECOMMAND_AI_SETTINGS", str(path))
    before = {name: os.environ.get(name) for name in ai_settings._MANAGED}
    for name in ai_settings._MANAGED:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ai_settings, "_APPLIED", {})
    yield path
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


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
    ai_settings.save({"backend": "openai", "baseUrl": "http://h/v1",
                      "apiKey": "sk-keep"})
    assert ai_settings.save({"backend": "openai", "baseUrl": "http://new/v1"}) \
        .key_for() == "sk-keep"
    assert ai_settings.save({"backend": "openai", "baseUrl": "http://new/v1",
                             "apiKey": ""}).key_for() == "sk-keep"


def test_clearing_takes_an_explicit_flag(store):
    ai_settings.save({"backend": "openai", "baseUrl": "http://h/v1",
                      "apiKey": "sk-drop"})
    assert ai_settings.save({"backend": "openai", "baseUrl": "http://h/v1",
                             "clearKey": True}).key_for() == ""
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


def test_a_backend_the_panel_can_configure_stays_selectable(store):
    """The closed loop @Triumph1701 found on #25: openai was disabled until a
    base URL existed, and Claude API until a key existed, but the boxes that
    set those only appear once the backend is selected. Disabled is reserved
    for what the panel cannot fix."""
    def entry(name):
        return [b for b in ai_settings.available_backends()
                if b["backend"] == name][0]
    for name in ("openai", "api"):
        assert entry(name)["available"] is True, name
        assert entry(name)["needs"], f"{name} should say what it still needs"
    ai_settings.save({"backend": "", "baseUrl": "http://127.0.0.1:8317/v1"})
    assert entry("openai")["needs"] == ""


# --- the UI surfaces which backend answered ---

def _ui() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()


def test_the_ui_shows_the_answering_backend():
    ui = _ui()
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
    # every backend takes a model now that the Claude ones are configurable,
    # but only two of them take a key and only one takes a base URL
    assert by_name["cli"]["needsModel"] and not by_name["cli"]["needsKey"]
    assert not by_name["cli"]["needsBaseUrl"]
    assert by_name["api"]["needsKey"] and by_name["api"]["needsModel"]
    assert not by_name["api"]["needsBaseUrl"]
    assert by_name["grok"]["needsModel"] and not by_name["grok"]["needsKey"]
    assert by_name["openai"]["needsBaseUrl"] and by_name["openai"]["needsModel"]


def test_the_claude_models_land_in_the_variables_the_planner_reads(store):
    import os
    ai_settings.save({"backend": "cli", "model": "opus"})
    assert os.environ.get("CLAUDE_CLI_MODEL") == "opus"
    assert planner.cli_model() == "opus"
    ai_settings.save({"backend": "api", "model": "claude-sonnet-5",
                      "apiKey": "sk-ant-x"})
    assert planner.api_model() == "claude-sonnet-5"
    assert "CLAUDE_CLI_MODEL" not in os.environ, \
        "a model for one backend must not leak into another"


def test_model_suggestions_come_with_their_source(store):
    """A list that cannot be overridden is worse than no list once it goes
    stale, so these are suggestions and say where they came from."""
    cli = ai_settings.list_models("cli")
    assert "sonnet" in cli["models"] and "opus" in cli["models"]
    assert cli["source"]
    assert ai_settings.list_models("openai")["source"] == "set a base URL first"


def test_grok_model_suggestions_survive_a_missing_binary(store, monkeypatch):
    monkeypatch.setattr(planner, "find_grok_cli", lambda: None)
    got = ai_settings.list_models("grok")
    assert got["models"] == []
    assert "not on this machine" in got["source"]


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


def test_every_model_box_is_optional(store):
    """Each backend has a default model, so a blank box is always valid."""
    for entry in ai_settings.available_backends():
        assert entry["modelOptional"] is True


def test_the_key_field_states_the_whole_rule(store):
    """A key an OAuth router never wanted should not send anyone hunting, and
    a key the Claude API cannot run without must not read as optional. One
    label covers both rather than trusting a per-backend word."""
    from pathlib import Path
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    assert "model (optional)" in ui
    assert "API key (required for Claude API but optional for others)" in ui
    assert "keyOptional" not in ui, "that flag drove the old per-backend label"


def test_the_model_source_line_is_set_not_appended(store):
    """It used to append to the note, so switching backends a few times
    stacked "Models from ..." several deep in one line."""
    from pathlib import Path
    ui = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text()
    assert "$('ainote').textContent +=" not in ui
    assert "aisrc" in ui, "the source line needs its own element"
    assert "if ($('aibackend').value !== backend) return;" in ui, \
        "a slow listing must not land under a different backend"


# --- outranking the environment is not erasing it (#25, finding 1) ---

def test_applying_a_choice_leaves_an_exported_key_alone(store, monkeypatch):
    """The one that defeated the feature: with ANTHROPIC_API_KEY exported and
    no file, server startup called apply_to_env() and the Claude API backend
    vanished from candidates() with nothing changed and nothing said."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")
    before = planner.candidates()
    assert "api" in before
    ai_settings.apply_to_env()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-shell"
    assert planner.candidates() == before


def test_an_exported_base_url_survives_a_save_that_does_not_mention_it(
        store, monkeypatch):
    """Same shape, and worse: PLANNER_BASE_URL was wiped and never persisted,
    so a router the user had configured by hand simply stopped being used."""
    monkeypatch.setenv("PLANNER_BASE_URL", "http://router.local/v1")
    ai_settings.save({"backend": "cli"})
    assert os.environ["PLANNER_BASE_URL"] == "http://router.local/v1"
    assert ai_settings.load().base_url == "http://router.local/v1"


def test_a_value_this_module_set_is_still_taken_back(store, monkeypatch):
    """Releasing must not become never letting go: the panel's own value has
    to disappear when the panel stops asking for it, or a stale model id
    steers a backend it was never meant for."""
    ai_settings.save({"backend": "grok", "model": "grok-4.6"})
    assert os.environ["GROK_CLI_MODEL"] == "grok-4.6"
    ai_settings.save({"backend": "cli", "model": ""})
    assert "GROK_CLI_MODEL" not in os.environ


def test_releasing_restores_what_the_panel_displaced(store, monkeypatch):
    """A panel value on top of an exported one, then the panel value goes: the
    user's own setting comes back rather than being collateral damage."""
    monkeypatch.setenv("GROK_CLI_MODEL", "grok-from-shell")
    ai_settings.save({"backend": "grok", "model": "grok-4.6"})
    assert os.environ["GROK_CLI_MODEL"] == "grok-4.6"
    ai_settings.save({"backend": "cli"})
    assert os.environ["GROK_CLI_MODEL"] == "grok-from-shell"


# --- the Claude API backend can be reached (#25, finding 2) ---

def test_claude_api_can_be_enabled_through_the_panel(client, store):
    """End to end through the endpoints the panel actually calls. It used to
    be unreachable: the option was disabled without a key, and the key box
    only appears once the option is selected."""
    entries = {b["backend"]: b for b in client.get("/api/ai-settings").json()["backends"]}
    assert entries["api"]["available"] is True
    assert entries["api"]["needsKey"] is True

    r = client.post("/api/ai-settings", json={"backend": "api",
                                              "apiKey": "sk-ant-typed"})
    assert r.status_code == 200
    assert r.json()["settings"] == {"backend": "api", "baseUrl": "",
                                    "model": "", "hasKey": True}
    assert planner.candidates() == ["api"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-typed"


def test_pinning_a_backend_that_cannot_run_is_refused_in_words(client, store):
    """Pinning disables fallthrough (#21), so an unconfigured pin buys a
    failed prompt later. Say so now instead."""
    r = client.post("/api/ai-settings", json={"backend": "api"})
    assert r.status_code == 400
    assert "Anthropic API key" in r.json()["error"]
    assert not store.exists(), "a refused save must not persist"

    r = client.post("/api/ai-settings", json={"backend": "openai",
                                              "baseUrl": ""})
    assert r.status_code == 400
    assert "base URL" in r.json()["error"]


def test_a_key_in_dot_env_is_enough_to_pin_the_api_backend(store, monkeypatch):
    """The check is against what will be in effect, not against the file."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")
    assert ai_settings.save({"backend": "api"}).backend == "api"


# --- only what the user typed gets written down (#25, finding 3) ---

def test_a_shell_key_is_never_copied_into_the_settings_file(store, monkeypatch):
    """Two problems in one: a secret the user chose to keep in their shell
    appears in a new file on disk unasked, and because the file outranks .env,
    rotating it there afterwards silently does nothing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-shell")
    ai_settings.save({"backend": "cli"})
    stored = json.loads(store.read_text())
    assert stored["keys"] == {}, stored
    assert "sk-ant-from-shell" not in store.read_text()


def test_rotating_a_key_in_the_environment_still_takes_effect(store, monkeypatch):
    """The consequence of the above, stated as the user would experience it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-first")
    ai_settings.save({"backend": "cli"})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-second")
    assert ai_settings.load().key_for("api") == "sk-second"


def test_an_env_model_is_not_pinned_into_the_file_either(store, monkeypatch):
    monkeypatch.setenv("GROK_CLI_MODEL", "grok-from-shell")
    ai_settings.save({"backend": "cli", "model": "opus"})
    stored = json.loads(store.read_text())
    assert stored["models"] == {"cli": "opus"}, stored


# --- model strings from an endpoint are not trusted markup (#25, findings 4, 5)

def test_the_answering_model_is_written_as_text_not_markup():
    ui = _ui()
    assert "by.textContent" in ui, "plan.model must not be inserted as HTML"
    assert "insertAdjacentHTML('afterend'" not in ui


def test_remote_model_ids_are_not_interpolated_into_an_attribute():
    ui = _ui()
    assert 'value="${m}"' not in ui, "an id with a quote breaks out of this"
    assert "opt.value = m" in ui


def test_plan_card_strings_are_escaped():
    """Every string on a card is model output, and the model can now be any
    endpoint the user configured."""
    ui = _ui()
    assert "esc(describe(a))" in ui
    assert "esc(a.reason" in ui


# --- listing models cannot hang the panel (#25, finding 6) ---

def test_listing_anthropic_models_is_bounded(store, monkeypatch):
    """The grok and endpoint listers time out at 20s and 10s. Without one here
    a hung network pins a threadpool worker for the SDK default plus retries,
    and the panel looks frozen rather than slow."""
    seen = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.models = self

        def list(self, **_):
            return type("L", (), {"data": [type("M", (), {"id": "claude-x"})()]})()

    monkeypatch.setitem(sys.modules, "anthropic",
                        type(sys)("anthropic"))
    sys.modules["anthropic"].Anthropic = FakeAnthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    out = ai_settings.list_models("api")
    assert out["models"] == ["claude-x"]
    assert seen.get("timeout"), "no timeout on the Anthropic client"
    assert seen["timeout"] <= 30
    assert seen.get("max_retries") is not None
