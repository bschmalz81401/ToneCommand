"""Issue #98: flag a request that asks for two opposed things at once.

"Tight but really warm and dark" is not one tone, it is two, and silently
picking one reading throws away half of what the player said without telling
them. This is deterministic on purpose: a live model call would be creative
about which reading it silently prefers, which is exactly the failure mode
#98 exists to close, and this run's own constraints rule out live model
calls for anything that has to be tested. So this is a small, curated table
of terms that genuinely pull in opposite directions on the FM9, each with a
documented default lean and the config/tone_rules.md rule it follows.

This does not try to be exhaustive - only pairs where config/tone_rules.md
already has an opinion get a lean. A pair not in the table is simply not
detected, which is the honest "we do not have a documented answer for this
one" rather than a guessed lean.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Tension:
    side_a: str              # the term actually found on side A
    side_b: str              # the term actually found on side B
    lean: str                # which side the tool will lean, stated plainly
    reason: str              # why, citing the rulebook

    def message(self) -> str:
        return (f"'{self.side_a}' and '{self.side_b}' pull in opposite "
                f"directions. Leaning {self.lean}: {self.reason}")


# Each row: (side-A terms, side-B terms, which side wins, why).
# Word-boundary matched, case-insensitive. Order does not matter; both sides
# are checked against the whole text.
_PAIRS: list[tuple[set[str], set[str], str, str]] = [
    ({"tight"},
     {"warm", "dark"},
     "tight",
     "rule 9 makes a rhythm's job to cut through a mix (tight, presence "
     "up, low end controlled); warmth/darkness comes from scooping the "
     "same presence a tight rhythm needs, so tight wins and warmth comes "
     "from cab/level choices instead of scooping presence"),
    ({"scooped", "mid-scooped"},
     {"mid-forward", "cutting", "cuts through"},
     "mid-forward",
     "rule 9 names a scooped/muddy rhythm as a failure outright, so a "
     "request for both scooped and cutting leans toward the one the "
     "rulebook does not reject"),
    ({"dry", "no reverb", "no effects"},
     {"ambient", "spacious", "wet", "lush"},
     "wet",
     "rule 8 requires a clean to be wet at minimum (delay + reverb "
     "always); rule 12 treats width/depth as part of what 'big' means, "
     "so ambience wins over dryness by default"),
    ({"quiet", "subtle", "low output"},
     {"loud", "huge", "in your face"},
     "loud",
     "rule 4 refuses an inaudible or unbalanced scene outright; nothing "
     "in the rulebook accepts a deliberately quiet scene as done"),
    ({"vintage", "old school"},
     {"modern high-gain", "high gain modern", "djent"},
     "vintage voicing",
     "rule 3 treats amp choice as the era/voicing decision and gain as a "
     "separate knob on that same amp, so this leans vintage VOICING with "
     "gain pushed as high as that voicing can take it, rather than "
     "switching to a different, modern-voiced amp"),
]


def _find(text: str, terms: set[str]) -> str | None:
    for term in terms:
        if re.search(r"\b" + re.escape(term) + r"\b", text, re.IGNORECASE):
            return term
    return None


def detect(request_text: str) -> list[Tension]:
    """Every documented opposed pair found in the request, worst first is not
    meaningful here so insertion order (table order) is preserved."""
    text = request_text or ""
    out: list[Tension] = []
    for side_a, side_b, lean, reason in _PAIRS:
        a = _find(text, side_a)
        b = _find(text, side_b)
        if a and b:
            out.append(Tension(a, b, lean, reason))
    return out


def context_lines(request_text: str) -> list[str]:
    """Lines to surface alongside a plan/response when a request is
    internally opposed, so the tension is STATED rather than silently
    resolved one way. Empty when nothing is detected."""
    tensions = detect(request_text)
    if not tensions:
        return []
    lines = ["\nREQUEST TENSION DETECTED. State this plainly in the summary "
             "before proposing anything; never silently pick a reading:"]
    for t in tensions:
        lines.append(f"- {t.message()}")
    return lines
