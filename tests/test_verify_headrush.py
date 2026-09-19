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


# --- review of #136: the poll helper, which replaced the flaking sleeps -----

class _RigClient:
    """Answers loadedName from a scripted sequence; 'boom' raises.

    Also answers get_properties for the chain, because a rig does not count as
    loaded until the chain stops changing: the unit flips the name while the
    previous rig's chain is still in place.
    """
    def __init__(self, sequence, chain_shapes=None):
        self.sequence = list(sequence)
        self.reads = 0
        # default: already quiet
        self.chain_shapes = list(chain_shapes or [{"Routing": 0}])
        self.chain_reads = 0

    def get_property(self, path, prop):
        self.reads += 1
        value = self.sequence[min(self.reads - 1, len(self.sequence) - 1)]
        if value == "boom":
            raise ConnectionError("transport blip")
        return value

    def get_properties(self, path):
        self.chain_reads += 1
        i = min(self.chain_reads - 1, len(self.chain_shapes) - 1)
        return self.chain_shapes[i]


def test_wait_for_rig_returns_once_the_name_matches_and_the_chain_is_quiet():
    client = _RigClient(["Old Rig", "Old Rig", "##HRB Wanted"])
    assert vh.wait_for_rig(client, "##HRB Wanted", timeout_s=5.0) == "##HRB Wanted"
    assert client.reads == 3


def test_wait_for_rig_does_not_accept_the_previous_rigs_chain():
    """The measured case, and the trap the first fix fell into.

    The unit flips the name while the PREVIOUS rig's chain is still in place,
    and that chain was measured sitting unchanged for about a second. It is
    perfectly quiet. Waiting for stillness alone therefore succeeds on the old
    chain and returns just as early as not waiting at all, so a write issued
    next still races the tail of the load.

    The old shape is held here for well past the quiet window, which is what
    the hardware did and what the previous test failed to encode.
    """
    old = (0, tuple([9] + [0] * 13))
    new = (0, tuple([4] + [0] * 13))
    held = [{"Routing": 0, **{f"ModuleType{n}": v for n, v in
                              enumerate(shape[1], start=1)}}
            for shape in [old] * 8 + [new] * 6]
    client = _RigClient(["##HRB Wanted"], chain_shapes=held)
    got = vh.wait_for_rig(client, "##HRB Wanted", timeout_s=8.0, before=old)
    assert got is not None, "it never settled"
    assert client.chain_reads > 8, (
        f"returned after {client.chain_reads} chain reads, while the previous "
        f"rig's chain was still in place")


def test_wait_for_rig_waits_out_the_ceiling_when_the_chain_cannot_change():
    """Reloading the same rig, or loading one with an identical chain, means
    the shape legitimately never differs. That must not hang or fail."""
    same = [{"Routing": 0, "ModuleType1": 4}] * 40
    client = _RigClient(["##HRB Wanted"], chain_shapes=same)
    before = (0, tuple([4] + [None] * 13))
    original = vh.REBUILD_CEILING_S
    vh.REBUILD_CEILING_S = 0.6                       # keep the test quick
    try:
        got = vh.wait_for_rig(client, "##HRB Wanted", timeout_s=6.0,
                              before=before)
    finally:
        vh.REBUILD_CEILING_S = original
    assert got is not None, "an unchanged chain was treated as a failed load"


def test_wait_for_rig_times_out_if_the_chain_never_settles():
    never = [{"Routing": 0, "ModuleType1": n} for n in range(200)]
    client = _RigClient(["##HRB Wanted"], chain_shapes=never)
    assert vh.wait_for_rig(client, "##HRB Wanted", timeout_s=1.0) is None


def test_wait_for_rig_tolerates_whitespace_the_unit_pads_with():
    client = _RigClient(["  ##HRB Wanted "])
    assert vh.wait_for_rig(client, "##HRB Wanted", timeout_s=5.0) is not None


def test_wait_for_rig_gives_up_and_says_so():
    client = _RigClient(["Never The Right One"])
    assert vh.wait_for_rig(client, "##HRB Wanted", timeout_s=0.3) is None


def test_wait_for_rig_backs_off_on_transport_errors(monkeypatch):
    """A `continue` with no sleep spins as fast as the process can, hammering
    a unit that is already struggling, for the whole timeout."""
    slept = []
    monkeypatch.setattr(vh.time, "sleep", lambda s: slept.append(s))
    client = _RigClient(["boom"])
    vh.wait_for_rig(client, "##HRB Wanted", timeout_s=0.2)
    assert slept, "the error path polled without backing off"
    assert client.reads <= len(slept) + 1


# --- review of #136: a refusal message must reflect what happened -----------

def test_expect_raise_builds_its_detail_after_the_call():
    """A formatted string is evaluated before `fn` runs, so a message quoting
    a counter reports the value from before the call and reads identically
    whether or not anything happened."""
    counter = {"n": 0}

    def refuse():
        counter["n"] += 1
        raise PermissionError("no")

    report = vh.Report()
    report.expect_raise("AC4", "counts after", PermissionError, refuse,
                        detail_ok=lambda: f"count is {counter['n']}",
                        detail_no="not refused")
    assert "count is 1" in report.rows[-1]["detail"], report.rows[-1]["detail"]


# --- review of #136: AC7 promises no rig id either --------------------------

def test_redactor_masks_rig_ids_too():
    vh.teach_redactor("10.0.0.5", ["##HRB One"], ["3f9a-uuid-2b71"])
    try:
        assert vh.redact("loadRig 3f9a-uuid-2b71 failed") == "loadRig <rig id> failed"
    finally:
        vh._SECRETS.clear()


# --- review of #136: an unreadable unit is not a clean one ------------------

def test_a_failed_dirty_read_does_not_green_the_restore(monkeypatch):
    client = _Client("##HRB Something")
    monkeypatch.setattr(vh, "HeadrushClient", lambda *a, **k: client)
    monkeypatch.setattr(vh, "HeadrushAdapter", lambda *a, **k: object())
    monkeypatch.setattr(vh, "load_registry", lambda: object())
    monkeypatch.setattr(vh, "verify", lambda *a, **k: None)
    # a unit that cannot be read reports None, which is falsy
    monkeypatch.setattr(vh, "discard_edits", lambda c: (True, None))
    rc = vh.main(["--host", "10.0.0.5"])
    vh._SECRETS.clear()
    assert rc == 1, "an unreadable dirty flag was treated as discarded"


def test_discard_edits_raises_when_the_rig_never_comes_back(monkeypatch):
    monkeypatch.setattr(vh, "wait_for_rig", lambda *a, **k: None)

    class C:
        def get_property(self, path, prop): return False
        def get_properties(self, path):
            return {"loadedName": "##HRB X", "AllRigNames": ["##HRB X"],
                    "AllRigIds": ["id"]}
        def call_method(self, path, method, args): return True

    with pytest.raises(TimeoutError):
        vh.discard_edits(C())


# --- review of #136: Ctrl-C is not an ordinary failing run ------------------

def test_keyboard_interrupt_restores_then_propagates(monkeypatch):
    client = _Client("##HRB Something")
    monkeypatch.setattr(vh, "HeadrushClient", lambda *a, **k: client)
    monkeypatch.setattr(vh, "HeadrushAdapter", lambda *a, **k: object())
    monkeypatch.setattr(vh, "load_registry", lambda: object())
    restored = []
    monkeypatch.setattr(vh, "discard_edits",
                        lambda c: restored.append(1) or (True, False))

    def interrupt(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(vh, "verify", interrupt)
    with pytest.raises(KeyboardInterrupt):
        vh.main(["--host", "10.0.0.5"])
    vh._SECRETS.clear()
    assert restored == [1], "Ctrl-C skipped the restore"
