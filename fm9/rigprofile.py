"""Design a tone for a rig you cannot see, without redistributing anyone's work.

A design needs to know what it is designing FOR: which blocks exist, how they
are wired, what the scenes are called. Without that it can only guess, and a
plan that names a block the receiver does not have is a plan that fails on
their unit rather than on yours.

The obvious way to share that is to send the whole snapshot. This does not do
that, on purpose.

WHY NOT JUST SHARE THE SNAPSHOT
-------------------------------
A full parameter dump of a preset IS the preset. Many presets on a real unit
came from paid packs, and docs/RECIPES.md states the project's position in one
line: nothing paid is ever redistributed. A snapshot with every value in it
would be a preset file wearing a different extension, and no amount of good
intent changes what the file contains.

So a profile carries STRUCTURE and not VALUES:

    which blocks are present, and on which grid cells
    how they are cabled
    what the eight scenes are called
    which blocks each scene has engaged, and on which channel

and never a single parameter value. That is enough to design against, and it
is not enough to reconstruct somebody's tone.

WHAT THAT COSTS, HONESTLY
-------------------------
Absolute requests work: "put a Brit 800 in, bypass the delay in scene 3, set
the cab to a greenback 4x12". Relative ones cannot: "bump the presence a bit"
is unanswerable, because the presence value is exactly what a profile leaves
out. The UI says so rather than quietly planning against a zero.
"""
from __future__ import annotations

import time

#: Bumped when the shape changes, so an old file is refused rather than
#: half-read into something that looks plausible.
PROFILE_VERSION = 1


def build(snapshot: dict, grid: dict | None = None, author: str = "") -> dict:
    """A shareable description of a preset's SHAPE. Never its values."""
    preset = snapshot.get("preset") or {}
    blocks = []
    for b in snapshot.get("blocks") or []:
        blocks.append({
            "family": b.get("family"),
            "instance": b.get("instance"),
            "label": b.get("label"),
            "bypassed": b.get("bypassed"),
            "channel": b.get("channel"),
            "channels": b.get("channels"),
        })
    out = {
        "profile_version": PROFILE_VERSION,
        "device": "FM9",
        "author": author,
        "captured": time.strftime("%Y-%m-%dT%H:%M:%S"),
        # The NAME of a preset is a label, not the work. The slot number is
        # dropped: it says where this sits on one person's unit, which is
        # meaningless on anyone else's and invites a store to the wrong place.
        "preset_name": preset.get("name"),
        "scenes": [{"number": s.get("number"), "name": s.get("name")}
                   for s in (snapshot.get("scenes") or [])],
        "blocks": blocks,
        # The amp and cab MODEL names are facts about which gear is emulated,
        # the same class of thing a recipe already carries, so they travel.
        "amp_model": (snapshot.get("values") or {}).get("AMP_MODEL"),
        "cab": (snapshot.get("values") or {}).get("cab"),
    }
    if grid and grid.get("cells"):
        out["grid"] = {
            "rows": grid.get("rows"), "cols": grid.get("cols"),
            "cells": [{"row": c["row"], "col": c["col"], "shunt": c["shunt"],
                       "family": c.get("family"), "instance": c.get("instance"),
                       "feeds": c.get("feeds", [])}
                      for c in grid["cells"]],
        }
    return out


def check(profile: dict) -> str | None:
    """Why this file cannot be used, or None if it can."""
    if not isinstance(profile, dict):
        return "that is not a profile"
    v = profile.get("profile_version")
    if v != PROFILE_VERSION:
        return (f"profile version {v!r}, this build reads "
                f"{PROFILE_VERSION}")
    if profile.get("device") != "FM9":
        return f"profile is for {profile.get('device')!r}, not an FM9"
    if not profile.get("blocks"):
        return "profile lists no blocks, so there is nothing to design against"
    return None


def as_blank_text() -> str:
    """Context for planning with no device and no reading at all.

    Refusing outright was wrong. "Give me a Steve Lukather lead tone in scene 4
    of a new preset" needs nothing from the rig: it is a build, not an edit,
    and every fact it needs is in the grounding catalogs. What it must not do
    is silently plan a RELATIVE request against a zero, so the planner is told
    exactly what it does and does not have, in the same shape as a profile.
    """
    return (
        "No device is connected and no preset has been read this session, so "
        "there is NO current state at all: no blocks, no scenes, no parameter "
        "values.\n"
        "You can still plan a request that stands on its own: building a tone "
        "from scratch, choosing amp and cab models by name, setting named "
        "values, naming a preset or a scene, storing to a numbered slot.\n"
        "You CANNOT plan anything relative, because there is nothing to be "
        "relative to. Anything of the form more, less, tighter, brighter, a "
        "bit of, or any instruction about a block you were not told exists, "
        "has to be refused rather than guessed at. Say what you would need "
        "read from the device to answer it.")


def as_state_text(profile: dict) -> str:
    """The planner's context, in the same shape server.state_text produces.

    Every line says what is present and what is NOT known, because the planner
    reads this as fact and the absence of values is itself a fact here.
    """
    lines = [f"Designing for a SHARED RIG PROFILE, not a connected device."]
    if profile.get("preset_name"):
        lines.append(f'Preset: "{profile["preset_name"]}"'
                     + (f' (shared by {profile["author"]})'
                        if profile.get("author") else ""))
    named = [s for s in profile.get("scenes") or [] if s.get("name")]
    if named:
        lines.append("Scenes: " + ", ".join(
            f'{s["number"]} "{s["name"]}"' for s in named))
    lines.append("Blocks in preset: " + ", ".join(
        f"{b['label']}{' (bypassed)' if b.get('bypassed') else ''} "
        f"ch{b.get('channel')}" for b in profile.get("blocks") or []))
    if profile.get("amp_model"):
        lines.append(f"Amp model: {profile['amp_model']}")
    if profile.get("cab"):
        lines.append(f"Cab: {profile['cab']}")
    lines.append(
        "NO PARAMETER VALUES ARE AVAILABLE. This profile carries structure "
        "only, deliberately, so that nobody's paid preset is redistributed. "
        "Absolute instructions can be planned (choose a model, bypass a "
        "block, set a named value). Relative ones cannot: there is no current "
        "gain, presence or mix to move up or down from. If the request needs "
        "a value you were not given, say so instead of inventing one.")
    return "\n".join(lines)
