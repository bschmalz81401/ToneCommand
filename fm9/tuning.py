"""The guitar's tuning, read from the player's own words (ToneCommand #138).

Deterministic and small on purpose: this never asks the model, and it only
knows the tunings IRCommand's TUNING_AXES knows, in IRCommand's own spelling,
so what it returns can go on the wire as `tuning=` unchanged.

  drop d, drop c, drop b, drop a, eb, d standard, c standard, b standard

Standard tuning (or no mention at all) is None: there is nothing to hint.
"""

from __future__ import annotations

import re

#: Canonical value -> the phrasings that mean it. Order matters within the
#: whole table: longer, more specific phrases are tried first so "drop c#"
#: is never read as "drop c" and "c# standard" never as "c standard".
TUNINGS: list[tuple[str, tuple[str, ...]]] = [
    ("drop c", ("drop c#", "drop c sharp", "drop db", "drop d flat", "drop dflat", "drop c")),
    ("drop b", ("drop b", "drop bb", "drop b flat", "drop a#", "drop a sharp")),
    ("drop a", ("drop a", "drop ab", "drop a flat", "drop g#")),
    ("drop d", ("drop d", "dropped d", "drop-d")),
    ("c standard", ("c# standard", "c sharp standard", "db standard", "d flat standard",
                    "c standard", "standard c", "tuned to c", "in c standard")),
    ("b standard", ("b standard", "standard b", "tuned to b", "in b standard", "bb standard", "b flat standard")),
    ("d standard", ("d standard", "standard d", "tuned to d", "in d standard", "whole step down", "full step down")),
    ("eb", ("eb standard", "e flat standard", "eb tuning", "e flat tuning", "half step down", "half-step down",
            "a half step down", "e flat", "e-flat", "eb", "tuned to eb", "in eb")),
]

_WORD = r"(?<![a-z0-9#])"
_END = r"(?![a-z0-9#])"


def _pattern(phrase: str) -> re.Pattern:
    body = re.escape(phrase).replace(r"\ ", r"[\s\-]+")
    return re.compile(_WORD + body + _END, re.IGNORECASE)


_PATTERNS: list[tuple[str, re.Pattern]] = [(value, _pattern(p)) for value, phrases in TUNINGS for p in phrases]


def parse_tuning(text: str | None) -> str | None:
    """The tuning the player named, in IRCommand's vocabulary, or None."""
    t = (text or "").lower()
    if not t.strip():
        return None
    t = re.sub(r"[,.;:!?()\[\]\"']", " ", t)
    # "standard" alone, or "e standard", is the default: nothing to hint.
    for value, pattern in _PATTERNS:
        if pattern.search(t):
            return value
    return None
