"""Every capture failure answered in one line that says what was done
instead (issue #150, I7). Never a dialog, a mode, or an error page.

Five paths. Each function returns the line; `guarded_install` is the one
that also acts: it stops at the first read-back mismatch and never retries.
The CPU rule that decides "too heavy" waits on I0 (#143), so it arrives
here as a verdict the caller already reached, not a threshold invented
here.
"""
from __future__ import annotations

from typing import Any, Callable

GIG_GATE_LINE = "GIG LOCK is on: refusing to touch the rig."


def no_capture(model_name: str, asked_for: str | None = None) -> str:
    what = f" for {asked_for}" if asked_for else ""
    return (f"no suitable capture{what} on file; built with the Fractal "
            f"{model_name} instead")


def too_heavy(capture_name: str, model_name: str,
              lighter_variant: str | None = None) -> str:
    if lighter_variant:
        return (f"{capture_name} is too heavy for this unit; used the lighter "
                f"variant {lighter_variant} of the same capture instead")
    return (f"{capture_name} is too heavy for this unit and no lighter variant "
            f"is offered; built with the Fractal {model_name} instead")


def no_nam_support(model_name: str, device_label: str = "this unit") -> str:
    return (f"{device_label} plays no captures (no NAM support on this "
            f"firmware); built with the Fractal {model_name} instead")


def gig_gate() -> str:
    """The existing refusal, word for word: it is already the one line."""
    return GIG_GATE_LINE


def readback_mismatch(slot: int, expected: str, note: str = "") -> str:
    why = f" ({note})" if note else ""
    return (f"capture install to slot {slot} stopped: the slot did not read "
            f"back as {expected}{why}; nothing else was changed and it was not "
            "retried. Check the slot on the unit before trying again")


def guarded_install(install: Callable[[Any, bytes, int], Any],
                    plan: list[tuple[Any, bytes, int]]) -> dict:
    """Run installs in order and STOP at the first one whose read-back is
    not verified. Returns what was done and the line for what was not; a
    build is never left half-applied silently, and nothing is retried."""
    done: list[int] = []
    for record, raw, slot in plan:
        try:
            result = install(record, raw, slot)
        except ValueError as e:                  # whitelist or reference refusal
            return {"done": done, "stopped_at": slot, "line": str(e)}
        if not getattr(result, "verified", False):
            expected = getattr(record, "name", None) or f"the capture for slot {slot}"
            return {"done": done, "stopped_at": slot,
                    "line": readback_mismatch(slot, expected,
                                              getattr(result, "note", ""))}
        done.append(slot)
    return {"done": done, "stopped_at": None,
            "line": f"{len(done)} capture(s) installed and read back"}


def answer(path: str, **facts) -> str:
    """One entry point for the planner side: the line for a named path."""
    if path == "no_capture":
        return no_capture(facts["model_name"], facts.get("asked_for"))
    if path == "too_heavy":
        return too_heavy(facts["capture_name"], facts["model_name"],
                         facts.get("lighter_variant"))
    if path == "no_nam_support":
        return no_nam_support(facts["model_name"], facts.get("device_label", "this unit"))
    if path == "gig_gate":
        return gig_gate()
    if path == "readback_mismatch":
        return readback_mismatch(facts["slot"], facts["expected"], facts.get("note", ""))
    raise KeyError(path)
