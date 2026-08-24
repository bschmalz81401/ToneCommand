"""Planner backend selection and the fall-through contract.

The three-class taxonomy is @Triumph1701's design from issue #7: transport or
malformed output is a backend failure and moves to the next candidate; a reply
that parses but says nothing is a planner result and must NOT fall through; an
aggregate error is raised only after every candidate is exhausted.

No network, no subprocess, no subscription: backends are stubbed.
"""
import pytest

from fm9 import planner


GOOD = {"summary": "more gain", "actions": [
    {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0}]}
EMPTY = {"summary": "", "actions": [], "clarification": None}
ASKS = {"summary": "", "actions": [], "clarification": "Which scene?"}


def run_with(monkeypatch, order, runners):
    monkeypatch.setattr(planner, "candidates", lambda: list(order))
    monkeypatch.setattr(planner, "_RUNNERS", runners)
    return planner.plan("more gain", "state", "reference")


def ok(plan_obj, model="stub"):
    return lambda *a, **k: (dict(plan_obj), model)


def fails(backend, failure_class="transport", detail="boom"):
    def runner(*a, **k):
        raise planner.BackendFailure(backend, failure_class, detail)
    return runner


# --- the failure taxonomy itself ---

def test_failure_class_must_be_known():
    with pytest.raises(ValueError, match="unknown failure class"):
        planner.BackendFailure("cli", "vibes", "nope")


def test_plan_quality_distinguishes_usable_from_silent():
    assert planner._plan_quality(GOOD) == "actions"
    assert planner._plan_quality(ASKS) == "clarification"
    assert planner._plan_quality(EMPTY) == "empty"


# --- candidate selection ---

def test_pinned_backend_disables_fallthrough(monkeypatch):
    monkeypatch.setenv("PLANNER_BACKEND", "grok")
    assert planner.candidates() == ["grok"]


def test_unknown_pin_is_refused_loudly(monkeypatch):
    monkeypatch.setenv("PLANNER_BACKEND", "gpt9")
    with pytest.raises(RuntimeError, match="not one of"):
        planner.candidates()


def test_no_candidates_names_every_way_to_configure_one(monkeypatch):
    monkeypatch.setattr(planner, "candidates", lambda: [])
    with pytest.raises(RuntimeError, match="PLANNER_BASE_URL"):
        planner.plan("p", "s", "r")


# --- fall-through contract ---

def test_backend_failure_moves_to_the_next_candidate(monkeypatch):
    got = run_with(monkeypatch, ["openai", "cli"],
                   {"openai": fails("openai", "transport", "connection refused"),
                    "cli": ok(GOOD, "sonnet")})
    assert got["backend"] == "cli"
    assert got["model"] == "sonnet"
    assert [(a["backend"], a["failure_class"]) for a in got["attempts"]] == [
        ("openai", "transport"), ("cli", None)]


def test_a_well_formed_but_empty_plan_does_not_burn_the_next_backend(monkeypatch):
    """The boundary that did not exist before: valid JSON saying nothing is a
    planner result, not a transport failure."""
    called = []

    def second(*a, **k):
        called.append("cli")
        return (dict(GOOD), "sonnet")

    got = run_with(monkeypatch, ["openai", "cli"],
                   {"openai": ok(EMPTY, "local"), "cli": second})
    assert got["plan_quality"] == "empty"
    assert got["backend"] == "openai"
    assert called == [], "a silent-but-valid reply must not trigger fallthrough"


def test_a_clarification_request_is_a_success_not_a_failure(monkeypatch):
    got = run_with(monkeypatch, ["cli"], {"cli": ok(ASKS)})
    assert got["plan_quality"] == "clarification"
    assert got["clarification"] == "Which scene?"


def test_unexpected_faults_are_recorded_as_backend_errors(monkeypatch):
    def explodes(*a, **k):
        raise ZeroDivisionError("bug in the backend")

    got = run_with(monkeypatch, ["openai", "cli"],
                   {"openai": explodes, "cli": ok(GOOD)})
    assert got["backend"] == "cli"
    assert got["attempts"][0]["failure_class"] == "backend_error"
    assert "bug in the backend" in got["attempts"][0]["detail"]


def test_aggregate_error_only_after_exhaustion_and_it_names_each_attempt(monkeypatch):
    monkeypatch.setattr(planner, "candidates", lambda: ["openai", "cli", "api"])
    monkeypatch.setattr(planner, "_RUNNERS", {
        "openai": fails("openai", "http_status", "502 bad gateway"),
        "cli": fails("cli", "backend_error", "expired login"),
        "api": fails("api", "unavailable", "no key"),
    })
    with pytest.raises(RuntimeError) as err:
        planner.plan("p", "s", "r")
    message = str(err.value)
    assert message.startswith("every planner backend failed")
    for fragment in ("502 bad gateway", "expired login", "no key",
                     "[http_status]", "[backend_error]", "[unavailable]"):
        assert fragment in message


def test_the_answering_backend_is_reported_on_the_plan(monkeypatch):
    """Deferring the settings UI is only acceptable if the plan says who
    answered (issue #20)."""
    got = run_with(monkeypatch, ["cli"], {"cli": ok(GOOD, "sonnet")})
    assert got["backend"] == "cli" and got["model"] == "sonnet"
    record = got["attempts"][-1]
    assert record["backend"] == "cli" and record["model"] == "sonnet"
    assert record["failure_class"] is None and record["detail"] == ""
    assert "target" in record, "a successful attempt records what it aimed at"


# --- subprocess environment ---

def test_cli_env_is_an_allowlist_not_a_copy():
    """A planner subprocess gets a working shell environment and its own
    credentials - not every other secret in the process."""
    source = {"PATH": "/usr/bin", "HOME": "/Users/x", "TERM": "xterm",
              "ANTHROPIC_API_KEY": "sk-ant-keep", "XAI_API_KEY": "xai-drop",
              "DATABASE_URL": "postgres://drop", "AWS_SECRET_ACCESS_KEY": "drop"}
    env = planner.cli_env(("ANTHROPIC_API_KEY",), source)
    assert env == {"PATH": "/usr/bin", "HOME": "/Users/x", "TERM": "xterm",
                   "ANTHROPIC_API_KEY": "sk-ant-keep"}


def test_cli_env_gives_each_binary_only_its_own_keys():
    source = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant",
              "XAI_API_KEY": "xai", "GROK_API_KEY": "grok"}
    claude = planner.cli_env(("ANTHROPIC_API_KEY",), source)
    grok = planner.cli_env(("XAI_API_KEY", "GROK_API_KEY"), source)
    assert "XAI_API_KEY" not in claude and "GROK_API_KEY" not in claude
    assert "ANTHROPIC_API_KEY" not in grok
    assert grok["XAI_API_KEY"] == "xai"


def test_cli_env_skips_empty_values():
    assert planner.cli_env(("ANTHROPIC_API_KEY",),
                           {"PATH": "/bin", "ANTHROPIC_API_KEY": ""}) == {"PATH": "/bin"}


# --- configuration parsing: must not take the app down or leak quotes ---

def test_a_malformed_timeout_does_not_break_the_import(isolated_env):
    """It used to be int() at module scope: a dotenv comment crashed
    `import fm9.planner`, and server.py imports it at startup."""
    isolated_env.write_text("PLANNER_TIMEOUT=300  # five minutes\n")
    assert planner.timeout_s() == 300
    isolated_env.write_text("PLANNER_TIMEOUT=banana\n")
    assert planner.timeout_s() == 180
    isolated_env.write_text('PLANNER_TIMEOUT="240"\n')
    assert planner.timeout_s() == 240
    isolated_env.write_text("PLANNER_TIMEOUT=-5\n")
    assert planner.timeout_s() == 180


def test_timeout_is_read_per_call_not_frozen_at_import(monkeypatch):
    monkeypatch.setenv("PLANNER_TIMEOUT", "42")
    assert planner.timeout_s() == 42
    monkeypatch.setenv("PLANNER_TIMEOUT", "43")
    assert planner.timeout_s() == 43


def test_quoted_env_values_are_unquoted(isolated_env):
    isolated_env.write_text('PLANNER_API_KEY="sk-local"\n'
                            "PLANNER_BACKEND='cli'\n"
                            'PLANNER_BASE_URL="http://127.0.0.1:8317/v1"\n')
    assert planner._env("PLANNER_API_KEY") == "sk-local"
    assert planner.candidates() == ["cli"]
    assert planner._openai_base_url() == "http://127.0.0.1:8317/v1"


def test_a_bare_env_file_is_not_an_anthropic_key(isolated_env):
    """A router-only install should not offer a doomed api candidate whose
    auth noise buries the actionable transport failure next to it."""
    isolated_env.write_text("PLANNER_BASE_URL=http://127.0.0.1:8317/v1\n")
    assert planner._api_available() is False
    # "cli" may legitimately be present: this host has the binary. What must
    # not appear is a doomed api candidate conjured by the file's existence.
    assert "api" not in planner.candidates()
    assert planner.candidates()[0] == "openai", "a configured router goes first"
    isolated_env.write_text("ANTHROPIC_API_KEY=sk-ant-real\n")
    assert planner._api_available() is True
    assert "api" in planner.candidates()


# --- the fall-through contract holds for badly shaped replies too ---

def test_valid_json_of_the_wrong_shape_is_a_backend_failure(monkeypatch):
    """{"actions": 42} parses fine and is a truthy non-iterable: validation
    raises, and that must fall through rather than kill the run."""
    monkeypatch.setattr(planner, "candidates", lambda: ["openai", "cli"])
    monkeypatch.setattr(planner, "_RUNNERS", {
        "openai": lambda *a, **k: ({"summary": "x", "actions": 42}, "local"),
        "cli": ok(GOOD, "sonnet")})
    got = planner.plan("p", "s", "r")
    assert got["backend"] == "cli"
    assert got["attempts"][0]["failure_class"] == "backend_error"
    assert got["attempts"][0]["model"] == "local"


def test_an_explicit_null_does_not_cost_the_whole_plan(monkeypatch):
    """instance and reason are not nullable on the Action model, and the
    prompt shows most siblings as nullable, which invites a null."""
    monkeypatch.setattr(planner, "candidates", lambda: ["cli"])
    monkeypatch.setattr(planner, "_RUNNERS", {"cli": ok(
        {"summary": "s", "actions": [
            {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE",
             "value": 6.0, "instance": None, "reason": None}]})})
    action = planner.plan("p", "s", "r")["actions"][0]
    assert action["instance"] == 1 and action["reason"] == ""


def test_an_unregistered_backend_is_a_clean_failure_not_a_keyerror(monkeypatch):
    monkeypatch.setattr(planner, "candidates", lambda: ["ghost", "cli"])
    got = run_with(monkeypatch, ["ghost", "cli"], {"cli": ok(GOOD)})
    assert got["backend"] == "cli"
    assert got["attempts"][0]["failure_class"] == "unavailable"


# --- subprocess environment, widened per review ---

def test_the_claude_path_keeps_proxy_and_enterprise_configuration():
    """Stripping these turned a working planner into a backend_error on any
    host behind a proxy, or routed through Bedrock."""
    source = {"PATH": "/bin", "HOME": "/h",
              "HTTPS_PROXY": "http://proxy:8080", "NODE_EXTRA_CA_CERTS": "/ca.pem",
              "CLAUDE_CONFIG_DIR": "/cfg", "ANTHROPIC_BASE_URL": "https://gw",
              "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "us-east-1",
              "GOOGLE_APPLICATION_CREDENTIALS": "/g.json",
              "XAI_API_KEY": "xai", "DATABASE_URL": "pg://x"}
    env = planner.cli_env(planner.CLAUDE_ENV_KEYS, source)
    for keep in ("HTTPS_PROXY", "NODE_EXTRA_CA_CERTS", "CLAUDE_CONFIG_DIR",
                 "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "AWS_REGION",
                 "GOOGLE_APPLICATION_CREDENTIALS"):
        assert keep in env, f"{keep} is configuration, not a foreign secret"
    assert "XAI_API_KEY" not in env and "DATABASE_URL" not in env


def test_the_grok_path_stays_narrow():
    source = {"PATH": "/bin", "XAI_API_KEY": "xai", "ANTHROPIC_API_KEY": "sk",
              "AWS_REGION": "us-east-1", "HTTPS_PROXY": "http://p:8080"}
    env = planner.cli_env(planner.GROK_ENV_KEYS, source)
    assert env == {"PATH": "/bin", "XAI_API_KEY": "xai"}


def test_the_cli_model_comes_from_modelusage():
    """Verified against a real envelope: there is no top-level model key."""
    assert planner.cli_envelope_model(
        {"modelUsage": {"claude-sonnet-4-6": {}}}) == "claude-sonnet-4-6"
    assert planner.cli_envelope_model({"modelUsage": {}}) == planner.CLI_MODEL


def test_quotes_and_a_trailing_comment_together(isolated_env):
    """Both are ordinary dotenv style, so they combine. Quotes must be found
    first or the value keeps them and fails in the usual silent ways."""
    assert planner._unquote('"240"  # five minutes') == "240"
    assert planner._unquote('"http://127.0.0.1:8317/v1"  # my router') \
        == "http://127.0.0.1:8317/v1"
    assert planner._unquote("'sk-local'  # key") == "sk-local"
    # a # inside the quotes is data, not a comment
    assert planner._unquote('"abc #def"') == "abc #def"
    isolated_env.write_text('PLANNER_TIMEOUT="240"  # five minutes\n'
                            'PLANNER_BASE_URL="http://127.0.0.1:8317/v1"  # mine\n')
    assert planner.timeout_s() == 240
    assert planner._openai_base_url() == "http://127.0.0.1:8317/v1"
