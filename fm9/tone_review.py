"""Pre-ship tone review: the deterministic half of the rulebook.

config/tone_rules.md is the spec the planner READS; this is the check that runs
on the RESULT, so the hard rules hold whether or not the planner followed the
prose. It is the code form of the rulebook's rule 14 self-check, aimed at the
failures that shipped in real builds (2026-09-04): cleans cut quiet, cleans with
no wet effects, and leads that do not out-saturate the rhythm so they sound
clean.

Deliberately pure and source-agnostic. It takes a per-scene summary - role plus
the few numbers a check needs - and returns findings. The summary can be built
from a plan's actions before sending (summary_from_plan) or read off the hardware
after applying; the checker does not care which, which is what makes it testable
without an FM9.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

#: Numeric floors per role, so "generous mix" is arithmetic rather than taste.
#: See config/tone_targets.json for why each number is what it is.
TARGETS_PATH = Path(__file__).resolve().parent.parent / "config" / "tone_targets.json"
CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "fm9_catalog.json"


def targets() -> dict:
    """The numeric policy, or an empty one if it is missing or unreadable.

    Absent targets mean the depth checks simply do not run, exactly as before
    they existed. A missing policy file must never take a build down.
    """
    try:
        return json.loads(TARGETS_PATH.read_text())
    except (OSError, ValueError):
        return {}


@dataclass
class Scene:
    """The little that a role check needs to know about one scene."""
    n: int
    name: str = ""
    role: str | None = None            # clean | rhythm | lead | None
    amp_gain: float | None = None      # DISTORT_DRIVE
    amp_level: float | None = None     # DISTORT_LEVEL
    scene_level: float | None = None   # OUTPUT_SCENEn
    effects: set[str] = field(default_factory=set)  # engaged families: DELAY, REVERB, ...
    boosted: bool = False              # a drive/boost engaged in front
    #: family -> mix/depth percent, when the plan sets one. Engagement alone was
    #: never enough: issue #50 shipped a "lush" clean with reverb at 12 percent,
    #: which passed an is-it-on check and was nearly dry.
    fx_mix: dict = field(default_factory=dict)
    boost_gain: float | None = None    # FUZZ_DRIVE, to catch a boost dialled
                                       # BELOW the rhythm it is meant to push
    #: block -> channel (0-3) and block -> bypassed, as the PLAN sets them.
    #: FM9 parameters live on the CHANNEL, not the scene, so what a scene
    #: actually stores is which blocks are bypassed and which channel each one
    #: is on. That makes these two the whole identity of a scene, which is why
    #: health.py can spot a duplicate after apply without reading a single
    #: parameter. Issue #51: the plan-time review threw them away, so a scene
    #: configured purely by set_channel was invisible here.
    channels: dict = field(default_factory=dict)
    bypass: dict = field(default_factory=dict)
    #: True once an engaged (non-bypassed) PEQ or GEQ block is seen in this
    #: scene's plan. Issue #97: a build must leave the player a real
    #: post-build fine-tune handle, so this is tracked the same way effects
    #: engagement already is.
    eq_engaged: bool = False

    def shape(self) -> tuple:
        """What this scene stores, and therefore what makes it itself.

        The plan-time twin of health._fingerprint. Empty when the plan says
        nothing structural about the scene, which is not the same as two
        scenes matching, and callers must not treat it as such.
        """
        return tuple(sorted(
            (b, bool(self.bypass.get(b)), self.channels.get(b))
            for b in set(self.channels) | set(self.bypass)))


@dataclass
class Finding:
    scene: int
    rule: str
    severity: str                       # "fail" (violates a hard rule) | "warn"
    message: str


def infer_role(name: str) -> str | None:
    """A scene's role from its name. None when it cannot be told, so a check is
    skipped rather than guessed."""
    n = (name or "").lower()
    if any(w in n for w in ("lead", "solo")):
        return "lead"
    if "clean" in n:
        return "clean"
    if any(w in n for w in ("rhythm", "crunch", "chug", "rhy")):
        return "rhythm"
    return None


def clones(scenes: list[Scene]) -> list[Finding]:
    """Rule 15: two scenes the plan makes structurally identical.

    Issue #51. A clone is a footswitch that does nothing on stage. health.py
    already catches it AFTER apply, from the same fact: FM9 parameters live on
    the CHANNEL, not the scene, so a scene's whole identity is which blocks are
    bypassed and which channel each one is on. Observed 2026-09-05 on a fresh
    80s build, the plan-time review passed clean and the post-apply scan then
    found scenes 4 and 8 identical, and after that 1 and 7.

    WHAT THIS CAN AND CANNOT CONCLUDE. A plan is a delta, so this sees only
    what the plan states, never the state a scene inherits. The finding is
    therefore worded as a fact about the PLAN, which is always true, rather
    than a prediction about the preset, which would not be. On a from-scratch
    build the two coincide, because a new preset's scenes all start identical,
    and that is exactly the case this was written for.

    A scene the plan says nothing structural about has an empty shape. Empty
    shapes are excluded rather than grouped: "the plan does not touch these
    two" is not evidence that they are the same, and treating it as a match
    would fire on every delta plan that renames a couple of scenes.
    """
    out: list[Finding] = []
    groups: dict[tuple, list[Scene]] = {}
    for s in scenes:
        shape = s.shape()
        if shape:
            groups.setdefault(shape, []).append(s)
    for members in groups.values():
        # Grouped, not pairwise, the same way health.py reports it: four
        # identical scenes are one problem, not six findings burying the rest.
        if len(members) < 2:
            continue
        nums = ", ".join(str(m.n) for m in members)
        for m in members[1:]:
            out.append(Finding(
                m.n, "15", "warn",
                f"scenes {nums} get the same blocks, bypass states and "
                f"channels from this plan; parameters live on the channel, so "
                f"nothing here makes scene {m.n} a different sound from scene "
                f"{members[0].n}, and its footswitch would do nothing"))
    return out


def _voiced(s: Scene) -> bool:
    """True once the plan has said something SUBSTANTIVE about this scene's
    TONE: a real gain/level/boost/depth value, or an explicit bypass state
    for some block. Deliberately excludes two things that are NOT tone
    voicing on their own:

    - A bare set_channel reassignment (channels populated, everything else
      empty) - that says WHICH channel a block sits on, not whether the
      scene has been voiced at all, which is exactly the case
      test_a_structural_finding_does_not_make_the_values_verified (#54)
      pins as "nothing was verified about how this sounds".
    - scene_level alone (OUTPUT_SCENEn). That is an output-level TRIM, the
      same category of fact as a channel assignment: it says how loud this
      scene is relative to the others, nothing about what it sounds like.
      A plan that only balances scene volume has not voiced a tone any
      more than one that only picks a channel has (explicit decision,
      independent review round 3: see
      test_scene_level_alone_is_not_tone_voicing).

    Rules 16/17 have no opinion on a scene the plan does not actually build.
    """
    return (s.amp_gain is not None or s.amp_level is not None
            or s.boost_gain is not None or bool(s.fx_mix) or bool(s.bypass))


def review(scenes: list[Scene]) -> list[Finding]:
    """Run the deterministic role checks and return what failed, worst first.

    The rhythm scenes are the reference the others are judged against (rule 4:
    rhythm is the loudness reference; rule 10: a lead out-saturates the rhythm).
    """
    out: list[Finding] = list(clones(scenes))

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    rhythm_gain = avg([s.amp_gain for s in scenes if s.role == "rhythm"])
    rhythm_level = avg([s.amp_level for s in scenes if s.role == "rhythm"])
    rhythm_boost = avg([s.boost_gain for s in scenes if s.role == "rhythm"])

    for s in scenes:
        fx = s.effects or set()
        if s.role == "clean":
            # rule 8: a clean is always wet - delay AND reverb at minimum
            missing = [e for e in ("DELAY", "REVERB") if e not in fx]
            if missing:
                out.append(Finding(s.n, "8", "fail",
                    f"clean scene has no {' or '.join(m.lower() for m in missing)}; "
                    "a big/80s clean needs delay + reverb"))
            # rule 8 / rule 4: a clean must not sit under its own rhythm.
            #
            # Judged RELATIONALLY, against this preset's own gain staging.
            # An absolute floor here used to fail every professional clean
            # (issue #65): AustinBuddy's sit at a median of -8.11 dB, which an
            # "at or below -4 is cut quiet" rule rejects outright. The absolute
            # value carries no information because DISTORT_LEVEL is an output
            # trim, and a harder-driven scene reads quieter by it.
            #
            # The relation does carry information, and separates the two cases
            # cleanly. The build that shipped wrong had a clean 6 dB BELOW its
            # rhythm; the professional presets put the clean roughly 1.5 dB
            # ABOVE theirs. Same absolute level, opposite verdicts.
            if s.amp_level is not None and rhythm_level is not None \
                    and s.amp_level < rhythm_level - 2:
                out.append(Finding(s.n, "8", "fail",
                    f"clean sits {rhythm_level - s.amp_level:.0f} dB below the "
                    "rhythm; a clean amp makes little output, so its level "
                    "belongs at or above the rhythm's, not under it"))
        elif s.role == "lead":
            # rule 10: audibly MORE saturated than the rhythm, not a hair more.
            # The rulebook's own example calls gain 7.8 over a 6.8 rhythm (+1.0)
            # too little for a lead, so the bar is a clear margin, ~+1.5.
            if s.amp_gain is not None and rhythm_gain is not None \
                    and s.amp_gain < rhythm_gain + 1.5:
                # A warning, not a failure. The tendency is real but not a law:
                # 5 of 13 professional presets miss this margin and two run the
                # lead BELOW the rhythm outright (issue #65), so blocking on it
                # rejects work that gigs.
                out.append(Finding(s.n, "10", "warn",
                    f"lead gain {s.amp_gain:g} is not clearly above the rhythm "
                    f"({rhythm_gain:g}); usually a lead out-saturates the "
                    "rhythm, though professional presets do vary"))
            # rule 4 hard cap: a lead more than ~4 dB over the rhythm
            if s.amp_level is not None and rhythm_level is not None \
                    and s.amp_level > rhythm_level + 4:
                out.append(Finding(s.n, "4", "warn",
                    f"lead sits {s.amp_level - rhythm_level:.0f} dB over the rhythm; "
                    "the cap is about +4, trim its level"))

    # Issue #50: an attempt to add absolute parameter floors here was tested
    # against 104 scenes of professionally voiced presets and refuted. It
    # raised 196 failures against gig-ready work, because DISTORT_LEVEL is an
    # output trim rather than loudness and a harder-driven scene reads quieter
    # by it. config/tone_targets.json records the measurements. The relational
    # lead-versus-rhythm idea already exists as rule 10 above, so nothing from
    # that attempt survives here.

    # whole-build: nothing inaudibly quiet (rule 4)
    for s in scenes:
        if s.amp_level is not None and s.amp_level <= -12:
            out.append(Finding(s.n, "4", "warn",
                f"amp level {s.amp_level:g} dB is very low; check it is not inaudible"))

    # Issue #96, rule 16: the bland test's own "bare amp with no boost where
    # one belongs" trigger, made real. A scene the plan leaves with zero
    # engaged effects AND no boost is exactly the never-ship-a-bare-preset
    # case - but only checkable once the plan has actually VOICED the scene
    # (some real value: gain, level, boost, fx depth, or a bypass state),
    # not merely reassigned which channel a block sits on. A first cut used
    # s.bypass alone as that guard, which missed the realistic case of a
    # scene built purely from set_param (amp gain/level) with no set_bypass
    # call at all - exactly a bare amp+cab build, and exactly what this rule
    # exists to catch.
    for s in scenes:
        if _voiced(s) and not s.effects and not s.boosted:
            out.append(Finding(s.n, "16", "fail",
                f"scene {s.n} is a bare amp+cab with nothing else engaged "
                "(no effects, no boost); never ship a generic preset - add "
                "the dimension the role needs (effects, boost, or both)"))

    # Issue #97, rule 17: every build leaves a real post-build fine-tune
    # handle. Whole-build, not per-scene: an EQ block is typically shared
    # infrastructure, not something every single scene needs its own copy
    # of, so one engaged EQ block anywhere in the build satisfies it. Same
    # _voiced guard as rule 16.
    #
    # WARN, not fail. NOT because the professional reference pack proves
    # real presets skip EQ - it does not capture PEQ/GEQ presence at all
    # (tests/data/austinbuddy_sample.json has no such field), so it is
    # silent on this question, not evidence either way (caught in
    # independent review: citing it as proof here would have been
    # overclaiming). The honest reason is the same PRECAUTION rule 10's
    # margin already applies for a genuinely unmeasured tendency (issue
    # #65): without real presence data to check this against, a hard fail
    # risks blocking real professional work on a dimension nobody has
    # actually verified matters as strictly as rule 16's bare-preset case
    # does. Warn until that data exists.
    if scenes and not any(s.eq_engaged for s in scenes) \
            and any(_voiced(s) for s in scenes):
        out.append(Finding(scenes[0].n, "17", "warn",
            "no EQ block (PEQ or GEQ) is engaged anywhere in this build; "
            "leave the player a real fine-tune handle to adjust to their "
            "ears/room/guitar without a rebuild"))

    order = {"fail": 0, "warn": 1}
    out.sort(key=lambda f: (order.get(f.severity, 2), f.scene))
    return out


def bland_test_passed(findings: list[Finding]) -> bool:
    """The gate issue #96 asks for: has the bland test (rule 16) actually
    passed, so a build can be proposed as-is. A "fail" on any other rule
    does not block this specifically - rule 16 is the bare-preset check;
    the other rules (8, 10, 15) have their own, separately surfaced meaning.
    """
    return not any(f.rule == "16" and f.severity == "fail" for f in findings)


# Independent review found TWO different failures from inferring meaning
# out of catalog parameter NAMES:
#
#   round 3: a hand-maintained short list (delay/reverb/chorus/flanger/
#   phaser/multitap) missed real catalog families entirely.
#
#   round 4: the fix for that ("any family with a _MIX param") was ALSO
#   wrong, in both directions at once. COMP/MULTICOMP/GATE/CROSSOVER/GEQ
#   all have a _MIX param but add no tonal dimension (a compressor or gate
#   is not what rule 16 means by "an effect"), so a scene with only those
#   engaged wrongly PASSED as not-bare - and GEQ specifically, despite
#   being declared interchangeable with PEQ for rule 17, silently also
#   satisfied rule 16, while PEQ (no _MIX param) correctly did not.
#   Meanwhile ENHANCER has only a _DEPTH param, no _MIX, so it fell out of
#   the _MIX-only derivation entirely and a genuinely voiced ENHANCER-only
#   scene wrongly FAILED as bare.
#
# "Has a blend parameter" is simply not the same fact as "is an audible
# tone-shaping dimension." There is no naming convention left to lean on,
# so this is an explicit, hand-classified, exhaustive table instead -
# the semantic judgment a name pattern cannot make for us. Every family
# the catalog gives a _MIX OR _DEPTH parameter to MUST appear here in
# exactly one category; completeness is enforced by
# test_every_catalogued_family_is_classified so a future roster addition
# fails loudly in CI rather than silently reopening either loophole.
#
#   audible   a real tone-shaping dimension: modulation, time-based,
#             pitch, filter/spatial, resonator, synth. Satisfies rule 16.
#   dynamics  level/gain-staging utility (compressor, gate, crossover
#             split). Real and useful, but adds no TONAL dimension, so it
#             does not satisfy rule 16 on its own.
#   eq        PEQ/GEQ. A DIFFERENT fine-tune handle, rule 17's concern,
#             not rule 16's - tracked separately via eq_engaged/_EQ so
#             engaging one does not silently also satisfy rule 16.
#   amp       DISTORT, the amp block itself: tracked via amp_gain/
#             amp_level, a different dimension than "effects".
#   boost     FUZZ, the dedicated boost/drive stage: tracked via
#             boost_gain/boosted, not "effects".
FAMILY_CLASS: dict[str, str] = {
    # audible: modulation
    "CHORUS": "audible", "FLANGER": "audible", "PHASER": "audible",
    "TREMOLO": "audible", "ROTARY": "audible", "RINGMOD": "audible",
    # audible: time-based (delay family)
    "DELAY": "audible", "MULTITAP": "audible", "MEGATAP": "audible",
    "TENTAP": "audible", "PLEX": "audible",
    # audible: pitch
    "PITCH": "audible", "FORMANT": "audible",
    # audible: filter / spatial / resonator / synth
    "FILTER": "audible", "REVERB": "audible", "RESONATOR": "audible",
    "SYNTH": "audible", "VOCODER": "audible", "WAH": "audible",
    "ENHANCER": "audible",
    # dynamics: level/gain-staging utility, not a tonal dimension
    "COMP": "dynamics", "MULTICOMP": "dynamics", "GATE": "dynamics",
    "CROSSOVER": "dynamics",
    # eq: rule 17's concern, not rule 16's
    "GEQ": "eq",
    # amp / boost: tracked via their own dedicated Scene fields
    "DISTORT": "amp", "FUZZ": "boost",
}

_BOOST = {"FUZZ", "DRIVE"}
#: EQ block families (issue #97). Both count as the same fine-tune handle;
#: a build needs at least one of either, not specifically both. PEQ has no
#: _MIX/_DEPTH param at all, so it never appears in FAMILY_CLASS - it is
#: reached only through set_bypass, never through the fx_mix/effects path.
_EQ = {"PEQ", "GEQ"}


def _catalog_mix_or_depth_families() -> frozenset[str]:
    """Every family the catalog actually gives a _MIX or _DEPTH parameter
    to - the exhaustive set FAMILY_CLASS must cover. Read fresh (not
    cached): this is a completeness CHECK, run once at test time, not a
    hot path."""
    try:
        data = json.loads(CATALOG_PATH.read_text())
        return frozenset(
            p["family"] for p in data["data"]["FM9_PARAMS"]
            if str(p.get("name", "")).upper().endswith(("_MIX", "_DEPTH")))
    except (OSError, ValueError, KeyError, TypeError):
        return frozenset()


#: A small, known-good fallback if the catalog cannot be read (missing
#: file, unexpected shape). Degrades to less coverage rather than raising -
#: same philosophy as targets() above: a missing/corrupt file must never
#: take a tone review down, only make it less complete. Unreachable in
#: practice once FAMILY_CLASS covers every real catalog family; kept as
#: the honest degrade path if the file itself ever goes missing.
_WET_FALLBACK = frozenset({"DELAY", "REVERB", "CHORUS", "FLANGER", "PHASER", "MULTITAP"})


@lru_cache(maxsize=1)
def wet_families() -> frozenset[str]:
    """Every family classified "audible" in FAMILY_CLASS - an explicit,
    exhaustive semantic judgment, not an inference from parameter naming
    (see the long comment above FAMILY_CLASS for why naming alone failed
    twice in independent review)."""
    if not _catalog_mix_or_depth_families():
        return _WET_FALLBACK
    return frozenset(fam for fam, cls in FAMILY_CLASS.items() if cls == "audible")


def summary_from_plan(actions: list[dict], reg=None) -> list[Scene]:
    """Best-effort per-scene summary from a plan's actions, for a check BEFORE
    anything is sent. A plan is a delta, so params it does not set are unknown
    (None) and their checks are simply skipped; the hardware-read summary after
    apply is the authoritative one. Actions are attributed to the scene active
    when they run (set_scene switches it), which is how a fresh build writes.
    """
    scenes: dict[int, Scene] = {}

    def scn(n: int) -> Scene:
        return scenes.setdefault(n, Scene(n=n))

    cur = None
    for a in actions:
        kind = a.get("kind")
        if kind == "set_scene":
            v = a.get("value")
            cur = int(v) if v is not None else cur
            continue
        if kind == "rename_scene":
            v = a.get("value")
            if v is not None:
                s = scn(int(v))
                s.name = a.get("type_name") or s.name
                s.role = infer_role(s.name)
            continue
        block = (a.get("block") or "").lower()
        if kind == "set_param" and cur is not None:
            p = (a.get("param") or "").upper()
            val = a.get("value")
            if p == "DISTORT_DRIVE":
                scn(cur).amp_gain = val
            elif p == "DISTORT_LEVEL":
                scn(cur).amp_level = val
            elif p == "FUZZ_DRIVE":
                scn(cur).boost_gain = val
                # Setting a boost's own gain is intent to use it, whether or
                # not this same plan also (re-)states set_bypass - a donor/
                # inherited channel can already be engaged, with the plan
                # only touching its level. Found in independent review: the
                # bland check (rule 16) otherwise read a plan that clearly
                # dials in a boost as having "no boost" simply because it
                # never repeated a bypass call the block did not need.
                if val is not None:
                    scn(cur).boosted = True
            elif p.endswith("_MIX") or p.endswith("_DEPTH"):
                fam = p.rsplit("_", 1)[0]
                # Depth is what makes an effect audible. A plan that engages
                # reverb and leaves it at 12 percent has not made a lush clean.
                if val is not None:
                    scn(cur).fx_mix[fam] = val
                    # Same reasoning as FUZZ_DRIVE above: dialling an
                    # effect's mix/depth is intent to use it, independent of
                    # whether this plan also touches that block's bypass.
                    if fam in wet_families():
                        scn(cur).effects.add(fam)
            elif p.startswith("OUTPUT_SCENE"):
                tail = p.replace("OUTPUT_SCENE", "")
                if tail.isdigit():
                    scn(int(tail)).scene_level = val
        elif kind == "set_bypass" and cur is not None:
            # Record the structural fact FIRST, for both directions. Only
            # engagement used to be kept, so a scene that differs from another
            # solely by what it BYPASSES read as identical to it.
            scn(cur).bypass[block] = bool(a.get("bypassed"))
            if a.get("bypassed") is False:
                fam = block.upper()
                # normalise a couple of friendly names
                fam = {"AMP": "DISTORT", "DRIVE": "FUZZ"}.get(fam, fam)
                if fam in wet_families():
                    scn(cur).effects.add(fam)
                if fam in _BOOST:
                    scn(cur).boosted = True
                if fam in _EQ:
                    scn(cur).eq_engaged = True
        elif kind == "set_channel" and cur is not None:
            # The action that was dropped entirely. A scene voiced purely by
            # pointing blocks at already-voiced channels sets no parameters,
            # so it created no Scene at all and every check skipped it.
            v = a.get("value")
            if v is not None:
                scn(cur).channels[block] = int(v)

    # fill roles for any scene named but not yet role'd
    for s in scenes.values():
        if s.role is None and s.name:
            s.role = infer_role(s.name)
    return [scenes[k] for k in sorted(scenes)]


def coverage(scenes: list[Scene]) -> dict:
    """What could actually be checked, so an empty result cannot pose as a pass.

    Issue #54: a plan is a delta, so any parameter it does not set is unknown
    and its check is skipped. That made one green result mean three different
    things at once: nothing was wrong, nothing could be checked, or the scene
    roles could not be inferred. The player could not tell which, and the
    dangerous one looked exactly like the safe one.

    Status is the honest summary of the whole review:
      verified        at least one check ran and had the facts it needed
      unknown         nothing could be checked
      not_applicable  there are no scenes to check
    """
    checks = {
        "role": lambda s: s.role is not None,
        "gain": lambda s: s.amp_gain is not None,
        "level": lambda s: s.amp_level is not None,
        "scene_level": lambda s: s.scene_level is not None,
        "effects": lambda s: bool(s.effects),
    }
    observed, missing = {}, {}
    for name, has in checks.items():
        seen = [s.n for s in scenes if has(s)]
        observed[name] = seen
        absent = [s.n for s in scenes if not has(s)]
        if absent:
            missing[name] = absent

    roles = [s.n for s in scenes if s.role]
    ran = sum(1 for name in checks if observed[name])
    # A role on its own is not a check, it is the precondition for one. Counting
    # it as coverage let a scene with a known role and no values at all report
    # "verified", which is precisely the overstatement this function exists to
    # prevent. At least one VALUE must have been observed.
    value_checks = [n for n in checks if n != "role" and observed[n]]
    if not scenes:
        status = "not_applicable"
    elif not roles or not value_checks:
        # No role means no role check can run, whatever else is known; no value
        # means there was nothing to judge that role against.
        status = "unknown"
    else:
        status = "verified"
    return {
        "status": status,
        "scenes": [s.n for s in scenes],
        "scenes_checked": roles,
        "roles_unknown": [s.n for s in scenes if not s.role],
        "checks_run": ran,
        "checks_possible": len(checks),
        "observed": observed,
        "missing": missing,
        "why": ("no scenes in this plan" if not scenes else
                "no scene role could be inferred, so no role check could run"
                if not roles else
                "the scene roles are known but no parameter value was, so "
                "there was nothing to judge them against"
                if not value_checks else
                f"{ran} of {len(checks)} checks had the facts they needed"),
    }


def findings_as_dicts(findings: list[Finding]) -> list[dict]:
    return [{"scene": f.scene, "rule": f.rule, "severity": f.severity,
             "message": f.message} for f in findings]
