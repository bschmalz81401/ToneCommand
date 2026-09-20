"""The FM9's global input routing, read and changed only the safe way
(issue #56, Gate 0).

Two globals matter for re-amping: Input 1 Source (GLOBAL param 72) and
Digital Input Source (param 73), both on effect id 1 (the system settings
block, #66). A temporary change goes through `temporary`: a journal file
first, the write with a read-back, the restore in `finally` with a
read-back, and the journal deleted; a server that starts with a journal
outstanding restores it before anything else. And no global is ever
written to an ordinal this unit was not observed at: the observed set is
what the hardware spike recorded (kb/HARDWARE_RULES.md), pinned here.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

GLOBAL_EFFECT_ID = 1
IN1_SOURCE = 72
DIGITAL_SOURCE = 73
PARAM_NAMES = {IN1_SOURCE: "Input 1 Source", DIGITAL_SOURCE: "Digital Input Source"}

#: Ordinals this code may write, per param: filled from OBSERVATION on the
#: unit (the spike reads each value the Pilot sets on the front panel).
#: Empty until the spike records them; an empty set refuses every write.
OBSERVED: dict[int, dict[int, str]] = {}

#: The observed table the Gate 0 spike pinned (2026-09-20, fw 11): Input 1
#: Source 0 = ANALOG, 1 = DIGITAL; Digital Input Source 1 = AES, 2 = USB.
#: Loaded at import so a fresh process (a restart restoring a journal) knows
#: what the unit has been seen to hold; TONECOMMAND_REAMP_OBSERVED moves it.
PINNED_FILE = Path(__file__).resolve().parent.parent / "config" / "reamp_observed.json"


class RoutingError(RuntimeError):
    """One line, written for the person at the rig."""


def journal_path() -> Path:
    override = os.environ.get("TONECOMMAND_ROUTING_JOURNAL", "").strip()
    return Path(override) if override else Path.home() / ".tonecommand" / "routing-journal.json"


def _spec(fm9, param_id: int):
    return fm9.reg.spec("GLOBAL", param_id, 1)


def read_routing(fm9: Any) -> dict[int, dict]:
    """{param: {ordinal, display}} for the two globals, from bulk_read of
    effect id 1 (one read, both values) plus the display name query."""
    values = fm9.bulk_read(GLOBAL_EFFECT_ID)
    if not values or len(values) <= DIGITAL_SOURCE:
        raise RoutingError("could not read the unit's global settings")
    out = {}
    for pid in (IN1_SOURCE, DIGITAL_SOURCE):
        display = None
        try:
            display = fm9.read_display_name(GLOBAL_EFFECT_ID, pid)
        except Exception:
            display = None
        out[pid] = {"ordinal": int(values[pid]), "display": display,
                    "name": PARAM_NAMES[pid]}
    return out


def observe(param_id: int, ordinal: int, display: str | None) -> None:
    """Record a value the unit was seen at, so it may be written later."""
    OBSERVED.setdefault(param_id, {})[int(ordinal)] = display or ""


def load_observed(table: dict) -> None:
    """Pin the observed table (from kb/HARDWARE_RULES.md's recorded values,
    via config) at import or test time: {param_id: {ordinal: display}}."""
    for pid, vals in table.items():
        for o, d in vals.items():
            observe(int(pid), int(o), d)


def load_pinned() -> int:
    """Load the pinned table from PINNED_FILE (or the env override) into
    OBSERVED. Returns how many values are observed afterwards; a missing or
    unreadable file loads nothing, and nothing is then writable."""
    override = os.environ.get("TONECOMMAND_REAMP_OBSERVED", "").strip()
    path = Path(override) if override else PINNED_FILE
    try:
        load_observed(json.loads(path.read_text()))
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return sum(len(v) for v in OBSERVED.values())


load_pinned()


#: The unit applies a write asynchronously (KNOWN_QUIRKS, settle window):
#: settle and retry before trusting the read-back, as the verified setters
#: and gallery_install._read_back do (#169).
READ_BACK_SETTLE = 0.15
READ_BACK_TRIES = 4


def _write(fm9, pid: int, ordinal: int) -> int:
    spec = _spec(fm9, pid)
    fm9.set_param_ordinal(spec, int(ordinal))
    got = -1
    for _ in range(READ_BACK_TRIES):
        time.sleep(READ_BACK_SETTLE)
        values = fm9.bulk_read(GLOBAL_EFFECT_ID)
        got = int(values[pid]) if values and len(values) > pid else -1
        if got == int(ordinal):
            return got
    raise RoutingError(f"{PARAM_NAMES[pid]} read back {got}, not {ordinal}")


def _write_journal(entries: list[dict]) -> None:
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps({"at": time.time(), "entries": entries}, indent=1))
    tmp.replace(path)


def _clear_journal() -> None:
    try:
        journal_path().unlink()
    except FileNotFoundError:
        pass


@contextmanager
def temporary(fm9: Any, changes: dict[int, int]):
    """Apply {param: ordinal} for the block's duration and put every value
    back afterwards, whatever happens inside. Journal first, so a process
    that dies mid-way leaves a record the next start restores from."""
    for pid, ordinal in changes.items():
        if pid not in PARAM_NAMES:
            raise RoutingError(f"global param {pid} is not one this code touches")
        if int(ordinal) not in OBSERVED.get(pid, {}):
            raise RoutingError(
                f"{PARAM_NAMES[pid]} ordinal {ordinal} was never observed on this "
                "unit; refusing to write a value nobody has seen it hold")
    before = read_routing(fm9)
    entries = [{"param": pid, "before": before[pid]["ordinal"], "after": int(o)}
               for pid, o in changes.items()]
    _write_journal(entries)
    applied: list[int] = []
    try:
        for e in entries:
            _write(fm9, e["param"], e["after"])
            applied.append(e["param"])
        yield {e["param"]: e for e in entries}
    finally:
        problems = []
        for e in reversed(entries):
            if e["param"] not in applied:
                continue
            try:
                _write(fm9, e["param"], e["before"])
            except Exception as exc:          # keep restoring the rest
                problems.append(f"{PARAM_NAMES[e['param']]}: {exc}")
        if problems:
            raise RoutingError("routing NOT fully restored: " + "; ".join(problems)
                               + "; the journal is kept for the next start")
        _clear_journal()


def restore_outstanding(fm9: Any) -> dict | None:
    """At server start: if a journal is outstanding, put every recorded
    'before' back, read it back, and report. None when there is nothing."""
    path = journal_path()
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise RoutingError(f"routing journal unreadable at {path}: {e}")
    restored, problems = [], []
    for e in doc.get("entries", []):
        pid, before = int(e["param"]), int(e["before"])
        name = PARAM_NAMES.get(pid, str(pid))
        # The same invariant as temporary(): a 'before' the unit was never
        # observed holding is not written, even from a journal, and the
        # journal is kept so the next start with the table loaded can.
        if pid not in PARAM_NAMES or before not in OBSERVED.get(pid, {}):
            problems.append(f"{name}: journal says {before}, which this unit was "
                            "never observed holding; not written")
            continue
        try:
            _write(fm9, pid, before)
            restored.append({"param": pid, "ordinal": before})
        except Exception as exc:
            problems.append(f"{name}: {exc}")
    if problems:
        raise RoutingError("routing journal restore incomplete: " + "; ".join(problems))
    _clear_journal()
    return {"restored": restored, "journal_at": doc.get("at")}
