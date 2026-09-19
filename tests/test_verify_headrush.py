"""Issue #134 review: the verification procedure's own guards.

The procedure is what decides whether the adapter met hardware, so a bug in it
is invisible in exactly the way that matters. Its first hardware run shipped two
vacuous greens that had to be rewritten, and these tests exist because that is
a pattern, not an accident: a check that cannot go red proves nothing.

Nothing here touches a device. The procedure's device-facing parts are exercised
against fakes; the hardware run is the separate thing these guard.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "verify_headrush", ROOT / "tools" / "verify_headrush.py")
vh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vh)


# --- AC7: redaction happens at print time, not by hand afterwards -----------

def test_redactor_masks_the_host_and_rig_names(capsys):
    vh.teach_redactor("10.0.0.5", ["##HRB Test One", "Someones Real Rig"])
    try:
        assert vh.redact("connecting to 10.0.0.5") == "connecting to <host>"
        assert vh.redact("loaded ##HRB Test One") == "loaded <##HRB name>"
        assert vh.redact("loaded Someones Real Rig") == "loaded <rig>"
    finally:
        vh._SECRETS.clear()


def test_a_detail_string_cannot_leak_a_name_through_record(capsys):
    """The earlier design redacted at the call sites, so anything built
    elsewhere - an exception carrying a url - reached the transcript."""
    vh.teach_redactor("10.0.0.5", ["##HRB Test One"])
    try:
        report = vh.Report()
        report.record("AC2", "x", True,
                      "HTTPError at http://10.0.0.5/api/v1 loading ##HRB Test One")
        out = capsys.readouterr().out
        assert "10.0.0.5" not in out and "Test One" not in out
        assert "<host>" in out and "<##HRB name>" in out
        assert "10.0.0.5" not in report.rows[0]["detail"]
    finally:
        vh._SECRETS.clear()


# --- a refusal check must require the RIGHT exception -----------------------

def test_expect_raise_passes_only_on_the_named_exception():
    report = vh.Report()
    report.expect_raise("AC4", "right", PermissionError,
                        lambda: (_ for _ in ()).throw(PermissionError("no")),
                        "refused", "not refused")
    assert report.rows[-1]["ok"] is True


def test_expect_raise_fails_on_a_different_exception():
    """A transport error used to green this row."""
    report = vh.Report()
    report.expect_raise("AC4", "wrong type", PermissionError,
                        lambda: (_ for _ in ()).throw(TimeoutError("network")),
                        "refused", "not refused")
    assert report.rows[-1]["ok"] is False
    assert "TimeoutError" in report.rows[-1]["detail"]


def test_expect_raise_fails_when_nothing_is_raised():
    report = vh.Report()
    report.expect_raise("AC4", "no raise", PermissionError,
                        lambda: None, "refused", "was NOT refused")
    assert report.rows[-1]["ok"] is False
    assert "NOT refused" in report.rows[-1]["detail"]


# --- AC4's counter has to count ---------------------------------------------

def test_counting_opener_counts_and_passes_through():
    seen = []
    opener = vh.CountingOpener(lambda *a, **k: seen.append(a) or b"body")
    assert opener.calls == 0
    assert opener("url", "GET", None, {}, 1.0) == b"body"
    assert opener.calls == 1 and len(seen) == 1


# --- a failing check must not end the run (AC5) ------------------------------

def test_check_turns_an_exception_into_a_failed_row_and_continues():
    report = vh.Report()
    report.check("AC2", "explodes", lambda: (_ for _ in ()).throw(ValueError("x")))
    report.check("AC2", "runs after", lambda: (True, "fine"))
    assert [r["ok"] for r in report.rows] == [False, True]
    assert report.failed and len(report.rows) == 2


# --- the ##HRB interlock ------------------------------------------------------

class _Client:
    """Answers loadedName and nothing else; any other call is a failure."""
    def __init__(self, name):
        self.name = name
        self.calls = []
        self._opener = lambda *a, **k: None

    def get_property(self, path, prop):
        self.calls.append((path, prop))
        return self.name

    def get_properties(self, path):
        self.calls.append((path, "*"))
        return {"AllRigNames": [self.name], "AllRigIds": ["id"],
                "loadedName": self.name}


def test_main_refuses_a_rig_that_is_not_a_test_preset(monkeypatch, capsys):
    client = _Client("Someones Real Rig")
    monkeypatch.setattr(vh, "HeadrushClient", lambda *a, **k: client)
    called = []
    monkeypatch.setattr(vh, "verify", lambda *a, **k: called.append(1))
    assert vh.main(["--host", "10.0.0.5"]) == 2
    assert not called, "the procedure ran against a non-test rig"
    assert "refusing to run" in capsys.readouterr().out


def test_main_runs_on_a_test_preset(monkeypatch):
    client = _Client("##HRB Something")
    monkeypatch.setattr(vh, "HeadrushClient", lambda *a, **k: client)
    monkeypatch.setattr(vh, "HeadrushAdapter", lambda *a, **k: object())
    monkeypatch.setattr(vh, "load_registry", lambda: object())
    monkeypatch.setattr(vh, "discard_edits", lambda c: (False, False))
    ran = []
    monkeypatch.setattr(vh, "verify", lambda *a, **k: ran.append(1))
    vh.main(["--host", "10.0.0.5"])
    assert ran == [1]
    vh._SECRETS.clear()


# --- the restore must survive a check that explodes --------------------------

def test_the_restore_runs_even_when_verify_raises(monkeypatch):
    """The finding this test exists for: `verify` was called bare, so any
    exception it did not convert to a row skipped the reload and left the
    unit holding the run's edits."""
    client = _Client("##HRB Something")
    monkeypatch.setattr(vh, "HeadrushClient", lambda *a, **k: client)
    monkeypatch.setattr(vh, "HeadrushAdapter", lambda *a, **k: object())
    monkeypatch.setattr(vh, "load_registry", lambda: object())
    restored = []
    monkeypatch.setattr(vh, "discard_edits",
                        lambda c: restored.append(1) or (True, False))

    def boom(*a, **k):
        raise RuntimeError("a check exploded")

    monkeypatch.setattr(vh, "verify", boom)
    rc = vh.main(["--host", "10.0.0.5"])
    assert restored == [1], "the unit was left dirty"
    assert rc == 1
    vh._SECRETS.clear()


def test_a_failed_restore_is_reported_rather_than_swallowed(monkeypatch):
    client = _Client("##HRB Something")
    monkeypatch.setattr(vh, "HeadrushClient", lambda *a, **k: client)
    monkeypatch.setattr(vh, "HeadrushAdapter", lambda *a, **k: object())
    monkeypatch.setattr(vh, "load_registry", lambda: object())
    monkeypatch.setattr(vh, "verify", lambda *a, **k: None)

    def cannot(*a, **k):
        raise ConnectionError("unit went away")

    monkeypatch.setattr(vh, "discard_edits", cannot)
    assert vh.main(["--host", "10.0.0.5"]) == 1
    vh._SECRETS.clear()
