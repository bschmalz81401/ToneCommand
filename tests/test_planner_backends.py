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
    assert got["attempts"][-1] == {"backend": "cli", "target": None,
                                   "model": "sonnet", "failure_class": None,
                                   "detail": ""}
