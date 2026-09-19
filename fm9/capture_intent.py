"""Capture or model: the deterministic half of rule 22 (issue #145).

The planner (the model) reads rule 22 in config/tone_rules.md. This module is
the part that must not depend on the model's mood: which way the player's
wording leans, and the one honest line that names the amp source in the
result. It never decides that a capture WAS used; only a CaptureRecord that
was actually selected for the plan can say that, and until the FM9 has a NAM
block (I4, #147) nothing selects one.

Matching is whole-phrase, on word boundaries, case-insensitive. "capture the
vibe" and "the actual settings" are not requests for a capture; "the actual
5150" is. When both classes appear, the one the player said LAST wins, and
a tie is no lean at all.
"""

from __future__ import annotations

import re

#: Phrases that ask for the real thing. `the actual` and `the real` only count
#: when followed by a gear word, so "the actual settings" stays neutral.
GEAR_WORDS = r"(?:amp|amplifier|head|rig|gear|combo|stack|5150|6505|jcm|plexi|recto|rectifier|mesa|marshall|fender|vox|friedman|orange|soldano|peavey|bogner|diezel|engl|evh|dumble|twin|deluxe|ac30|jtm|slo|uberschall|herbert|powerball|invective)"
CAPTURE_PHRASES = (
    r"the real amp", r"the real thing", r"sound like the real",
    rf"the actual {GEAR_WORDS}", rf"the real {GEAR_WORDS}",
    r"a capture of", r"captured", r"nam capture", r"the capture",
)
#: Phrases that ask for a starting point to shape.
MODEL_PHRASES = (
    r"base tone", r"starting point", r"something i can tweak", r"to shape", r"to tweak", r"a base to",
    r"a base i can", r"stock model", r"the model(?! number)", r"fractal model", r"factory model",
)


def _last_match(phrases: tuple[str, ...], text: str) -> int:
    """The end position of the last occurrence of any phrase, or -1."""
    last = -1
    for phrase in phrases:
        for m in re.finditer(rf"(?<![a-z0-9]){phrase}(?![a-z0-9])", text):
            last = max(last, m.end())
    return last


def amp_source_intent(words: str | None) -> str | None:
    """'capture', 'model' or None from the player's own words."""
    text = re.sub(r"\s+", " ", (words or "").lower())
    if not text.strip():
        return None
    capture_at = _last_match(CAPTURE_PHRASES, text)
    model_at = _last_match(MODEL_PHRASES, text)
    if capture_at < 0 and model_at < 0:
        return None
    if capture_at == model_at:
        return None
    return "capture" if capture_at > model_at else "model"


def amp_source_line(intent: str | None, capture_support: bool, used_capture, model_name: str | None) -> str:
    """The result line naming the amp source. A capture is claimed ONLY for
    `used_capture`, the CaptureRecord actually selected for the plan;
    `capture_support` alone (the unit could play one) never produces it."""
    if used_capture is not None:
        gear = getattr(used_capture, "gear", None) or "gear not on file"
        who = getattr(used_capture, "modeled_by", None)
        return f"amp is a capture of a real {gear}" + (f" by {who}" if who else " (modeled_by not on file)")
    line = f"amp is the Fractal {model_name}" if model_name else "amp is the Fractal model already loaded"
    if intent == "capture":
        line += ", no NAM support on this unit yet" if not capture_support else ", no suitable capture on file"
    return line


def amp_source(words: str | None, *, capture_support: bool, used_capture=None, model_name: str | None = None) -> dict:
    """What a plan result carries under `amp_source`."""
    intent = amp_source_intent(words)
    return {"intent": intent, "kind": "capture" if used_capture is not None else "model",
            "line": amp_source_line(intent, capture_support, used_capture, model_name)}
