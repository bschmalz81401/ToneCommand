"""Resolve "sound like Devin Townsend" to a Gift of Tone entry (issue #158,
J5), deterministically and honestly.

The command bar sees four shapes: "sound like X", "give me X's rig",
"install the X pack", "X tones". The name in them is matched against the
catalog's `artists` lists with three rules and nothing fuzzier:

- every word of the phrase is a whole word of an entry's artists, so a
  full name, a first name alone ("Devin" today; "Mark" is two people and
  asks), a surname alone and a band name (Periphery) all resolve, and a
  fragment ("own") never does;
- a phrase that IS an artist's full name beats entries that merely
  contain its words (Justin York, not Justin Derrico too).

Two or more candidates is a question naming them, never a guess. No
candidate is `absent`, and the line for that says plainly that no official
pack exists, so the planner's build that follows is never labelled as the
artist's own (config/tone_rules.md rule 23).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .acquire import _STOPWORDS

#: The shapes the command bar recognises, each capturing the name.
_SHAPES = (
    re.compile(r"\bsounds?\s+like\s+(?P<name>.+?)\s*$", re.I),
    re.compile(r"\b(?P<name>.+?)(?:'s|s')\s+(?:rig|tone|tones|sound|preset|presets|pack)\b", re.I),
    re.compile(r"\b(?:install|get|load|grab|fetch|give\s+me)\s+(?:the\s+)?(?P<name>.+?)\s+(?:pack|bundle|presets?|tones?|rig)\b", re.I),
    re.compile(r"\b(?P<name>[A-Za-z][\w' ]+?)\s+(?:tones?|presets?|pack|rig)\b", re.I),
)

#: Words that say how the player asked, never who: stripped from the name.
_ASK_WORDS = frozenset("like sound sounds give gimme want wanna play make use "
                       "show try hear".split())

_TRAIL = re.compile(r"\b(?:from|on|off|please|now|again|to|into)\b.*$", re.I)


def artist_phrase(said: str) -> str | None:
    """The name a sentence asks for, or None when it asks for nothing of
    the sort. Stopwords and the destination clause are stripped, so
    "install the Periphery pack to preset 4" yields "periphery"."""
    text = " ".join(str(said or "").split())
    if not text:
        return None
    for shape in _SHAPES:
        m = shape.search(text)
        if not m:
            continue
        name = _TRAIL.sub("", m.group("name")).strip(" ,.!?\"'")
        words = [w for w in re.findall(r"[a-z0-9']+", name.lower())
                 if w not in _STOPWORDS and w not in _ASK_WORDS]
        words = [w.rstrip("'") for w in words if w.rstrip("'")]
        if words:
            return " ".join(words)
    return None


@dataclass
class Resolution:
    status: str                       # resolved | ambiguous | absent
    phrase: str
    entry: dict | None = None
    candidates: list[dict] = field(default_factory=list)
    question: str | None = None
    line: str | None = None


def _artists(entry: dict) -> list[str]:
    return [str(a) for a in (entry.get("artists") or [])]


def _label(entry: dict) -> str:
    return ", ".join(_artists(entry)) + f" ({entry.get('year')})"


def resolve(phrase: str, entries: list[dict]) -> Resolution:
    words = [w for w in phrase.lower().split() if w]
    if not words:
        return Resolution("absent", phrase, line=absent_line(phrase))
    # exact: every word of the phrase is a whole word of the entry's artists
    # ("vai" finds Steve Vai; "own" never finds Devin Townsend)
    def _tokens(e):
        return set(re.findall(r"[a-z0-9']+", " ".join(_artists(e)).lower()))
    exact = [e for e in entries if all(w in _tokens(e) for w in words)]
    # a full artist name typed exactly beats a looser containment (Justin
    # York vs Justin Derrico both contain "justin", only one is "justin york")
    full = [e for e in exact
            if any(" ".join(re.findall(r"[a-z0-9']+", a.lower())) == " ".join(words)
                   for a in _artists(e))]
    candidates = full or exact
    if not candidates:
        return Resolution("absent", phrase, line=absent_line(phrase))
    # one artist, several years (a returning artist): newest wins, no question
    names = {tuple(_artists(e)) for e in candidates}
    if len(names) == 1:
        best = max(candidates, key=lambda e: (e.get("year") or 0, str(e.get("number"))))
        return Resolution("resolved", phrase, entry=best)
    ordered = sorted(candidates, key=lambda e: (
        -(e.get("year") or 0), re.sub(r"[^a-z0-9 ]", "", _label(e).lower())))
    return Resolution("ambiguous", phrase, candidates=ordered,
                      question=ambiguous_question(phrase, ordered))


def ambiguous_question(phrase: str, candidates: list[dict]) -> str:
    names = " or ".join(_label(e) for e in candidates)
    return f"Which {phrase.title()}: {names}?"


def absent_line(phrase: str) -> str:
    who = phrase.title() if phrase else "that artist"
    return (f"There is no official Gift of Tone pack for {who}; building a "
            "tone in that style from what I know, which is my interpretation, "
            "not their preset.")
