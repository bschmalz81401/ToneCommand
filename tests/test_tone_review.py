"""The deterministic pre-ship tone check (fm9/tone_review.py): the enforcement
half of config/tone_rules.md rule 14. It must catch the failures that actually
shipped on 2026-09-04 (cleans cut quiet and dry, leads that do not out-saturate
the rhythm) and stay quiet on a build that is done right.
"""
import pytest

from fm9 import tone_review as tr
from fm9.tone_review import Scene


def _by_rule(findings):
    return {(f.scene, f.rule) for f in findings}


def test_infer_role_reads_the_scene_name():
    assert tr.infer_role("Sykes Clean") == "clean"
    assert tr.infer_role("Sykes Rhythm") == "rhythm"
    assert tr.infer_role("Sykes Dry Lead") == "lead"
    assert tr.infer_role("Solo") == "lead"
    assert tr.infer_role("Scene 4") is None


def test_it_catches_the_sykes_failure():
    """The exact build that shipped wrong: cleans at -8 with no wets, a lead at
    gain 7.8 barely over a 6.8 rhythm."""
    scenes = [
        Scene(1, "Sykes Clean", "clean", amp_gain=2.8, amp_level=-8, effects={"REVERB"}),
        Scene(3, "Sykes Rhythm", "rhythm", amp_gain=6.8, amp_level=-2),
        Scene(5, "Sykes Dry Lead", "lead", amp_gain=7.8, amp_level=-2),
    ]
    f = tr.review(scenes)
    rules = _by_rule(f)
    assert (1, "8") in rules, "did not flag the dry clean (no delay)"
    # The clean is caught RELATIONALLY now, not by an absolute floor. It sits
    # 6 dB under its own rhythm, which is the thing that was actually wrong.
    # The old "at or below -4 dB is cut quiet" test failed every professional
    # clean as well (issue #65): AustinBuddy's sit at a median of -8.11 dB, and
    # ABOVE their own rhythms, so the absolute value carried no information.
    assert any(f_.scene == 1 and "below the rhythm" in f_.message for f_ in f), \
        "did not flag the clean sitting under its own rhythm"
    assert (5, "10") in rules, "did not flag the under-saturated lead"
    assert all(x.severity == "fail" for x in f if x.rule == "8")
    # Rule 10 warns rather than fails: 5 of 13 professional presets miss the
    # +1.5 margin and two run the lead below the rhythm, so blocking on it
    # rejects work that gigs.
    assert all(x.severity == "warn" for x in f if x.rule == "10")


def test_a_good_build_passes_clean():
    scenes = [
        Scene(1, "Big Clean", "clean", amp_gain=2.5, amp_level=1,
              effects={"CHORUS", "DELAY", "REVERB"}, eq_engaged=True),
        Scene(2, "Rhythm", "rhythm", amp_gain=6.5, amp_level=-2, effects={"REVERB"}),
        Scene(3, "Lead", "lead", amp_gain=8.5, amp_level=0,
              effects={"DELAY", "REVERB"}, boosted=True),
    ]
    assert tr.review(scenes) == []


def test_a_lead_only_a_hair_over_the_rhythm_is_flagged():
    scenes = [
        Scene(1, "Rhythm", "rhythm", amp_gain=7.0, amp_level=-2),
        Scene(2, "Lead", "lead", amp_gain=7.2, amp_level=-2, effects={"DELAY", "REVERB"}),
    ]
    assert (2, "10") in _by_rule(tr.review(scenes))


def test_unknown_role_is_skipped_not_guessed():
    # no role -> no ROLE-SPECIFIC findings (rules 8, 10). Rule 16 (bland) is
    # role-agnostic and correctly still fires: this scene has real gain/level
    # but no effects and no boost, which is a bare amp+cab regardless of
    # whether its role could be inferred.
    f = tr.review([Scene(4, "Scene 4", None, amp_gain=2.0, amp_level=-8)])
    assert not [x for x in f if x.rule in ("8", "10")]
    assert {x.rule for x in f} == {"16", "17"}


def test_summary_from_plan_extracts_scene_state():
    actions = [
        {"kind": "rename_scene", "value": 1, "type_name": "Clean"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 2.5},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_LEVEL", "value": 1},
        {"kind": "set_bypass", "block": "delay", "bypassed": False},
        {"kind": "set_bypass", "block": "reverb", "bypassed": False},
        {"kind": "rename_scene", "value": 2, "type_name": "Lead"},
        {"kind": "set_scene", "value": 2},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 9},
        {"kind": "set_param", "block": "output", "param": "OUTPUT_SCENE2", "value": 3},
    ]
    scenes = {s.n: s for s in tr.summary_from_plan(actions)}
    assert scenes[1].role == "clean" and scenes[1].amp_gain == 2.5 and scenes[1].amp_level == 1
    assert scenes[1].effects == {"DELAY", "REVERB"}
    assert scenes[2].role == "lead" and scenes[2].amp_gain == 9 and scenes[2].scene_level == 3


def test_findings_as_dicts_is_json_shaped():
    f = tr.review([Scene(1, "Clean", "clean", amp_level=-8, effects=set())])
    d = tr.findings_as_dicts(f)
    assert d and all(set(x) == {"scene", "rule", "severity", "message"} for x in d)


# --- rule 15: scene clones, caught BEFORE send (issue #51) ---------------
#
# A clone is a footswitch that does nothing on stage. health.py already found
# them after apply; the plan-time review could not see them at all, because
# set_channel was dropped on the floor and only ENGAGEMENT was recorded from
# set_bypass. Observed 2026-09-05 on a fresh 80s build: the plan passed clean,
# then the post-apply scan found scenes 4 and 8 identical, and after fixing
# that, scenes 1 and 7.

def _scene_actions(n, name, amp_ch, rev_ch, delay_bypassed):
    return [
        {"kind": "rename_scene", "value": n, "type_name": name},
        {"kind": "set_scene", "value": n},
        {"kind": "set_channel", "block": "amp", "value": amp_ch},
        {"kind": "set_channel", "block": "reverb", "value": rev_ch},
        {"kind": "set_bypass", "block": "delay", "bypassed": delay_bypassed},
    ]


def test_a_scene_voiced_only_by_set_channel_is_visible():
    """The root cause. Such a scene sets no parameters, so it produced no
    usable summary and every check silently skipped it."""
    scenes = {s.n: s for s in tr.summary_from_plan(
        _scene_actions(4, "Lead", 2, 1, False))}
    assert 4 in scenes
    assert scenes[4].channels == {"amp": 2, "reverb": 1}
    assert scenes[4].bypass == {"delay": False}


def test_two_identical_scenes_are_flagged_before_send():
    """The exact 2026-09-05 failure: scenes 4 and 8 the same sound twice."""
    plan = (_scene_actions(3, "Rhythm", 1, 0, True)
            + _scene_actions(4, "Lead", 2, 1, False)
            + _scene_actions(8, "Solo", 2, 1, False))
    f = tr.review(tr.summary_from_plan(plan))
    clone = [x for x in f if x.rule == "15"]
    assert clone, "the plan-time review missed a clone again"
    assert "4" in clone[0].message and "8" in clone[0].message


def test_a_clone_is_a_warn_not_a_fail():
    """It is a statement about the PLAN. On a delta the scenes could still
    differ in inherited state, so it must not block a send."""
    plan = (_scene_actions(4, "Lead", 2, 1, False)
            + _scene_actions(8, "Solo", 2, 1, False))
    assert all(x.severity == "warn"
               for x in tr.review(tr.summary_from_plan(plan)) if x.rule == "15")


def test_scenes_on_different_channels_are_not_clones():
    plan = (_scene_actions(4, "Lead", 2, 1, False)
            + _scene_actions(8, "Solo", 3, 1, False))
    assert not [x for x in tr.review(tr.summary_from_plan(plan))
                if x.rule == "15"]


def test_scenes_differing_only_by_a_bypass_are_not_clones():
    """Bypass is half of a scene's identity. Recording engagement alone made
    a scene that differs solely by what it turns OFF look identical."""
    plan = (_scene_actions(4, "Lead", 2, 1, False)
            + _scene_actions(8, "Solo", 2, 1, True))
    assert not [x for x in tr.review(tr.summary_from_plan(plan))
                if x.rule == "15"]


def test_scenes_the_plan_says_nothing_structural_about_are_not_clones():
    """A delta plan that renames two scenes must not report them as the same
    sound. 'The plan does not touch these' is not evidence that they match."""
    plan = [{"kind": "rename_scene", "value": 1, "type_name": "Clean"},
            {"kind": "rename_scene", "value": 2, "type_name": "Crunch"}]
    assert not [x for x in tr.review(tr.summary_from_plan(plan))
                if x.rule == "15"]


def test_a_group_of_clones_is_reported_once_per_extra_scene():
    """Grouped, not pairwise, the same way health.py reports it: three
    identical scenes are one problem, not three findings burying the rest."""
    plan = (_scene_actions(2, "A", 2, 1, False)
            + _scene_actions(4, "B", 2, 1, False)
            + _scene_actions(6, "C", 2, 1, False))
    clone = [x for x in tr.review(tr.summary_from_plan(plan)) if x.rule == "15"]
    assert len(clone) == 2, "should name the group, not every pair"
    assert {x.scene for x in clone} == {4, 6}


def test_the_plan_shape_is_the_same_identity_health_uses():
    """health._fingerprint is (effect_id, bypassed, channel) read off the
    device. This is the plan-time twin, and it must stay the same idea: a
    scene IS its blocks, their bypass states and their channels."""
    s = tr.Scene(1, "X", None)
    s.channels = {"amp": 2}
    s.bypass = {"amp": False, "delay": True}
    assert s.shape() == (("amp", False, 2), ("delay", True, None))
    assert tr.Scene(2, "Y", None).shape() == (), "an untouched scene is empty"


# --- issue #96: the bland test (rule 16) as a real gate --------------------

def test_bare_amp_cab_only_scene_fails_the_bland_test():
    """A scene the plan leaves with nothing engaged - no effects, no boost -
    is exactly the bare amp+cab preset issue #96 says must never ship."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_channel", "block": "amp", "value": 1},
        {"kind": "set_bypass", "block": "delay", "bypassed": True},
        {"kind": "set_bypass", "block": "reverb", "bypassed": True},
    ]
    scenes = tr.summary_from_plan(plan)
    findings = tr.review(scenes)
    bland = [f for f in findings if f.rule == "16"]
    assert len(bland) == 1
    assert bland[0].severity == "fail"
    assert bland[0].scene == 1
    assert not tr.bland_test_passed(findings)


def test_a_scene_voiced_only_by_set_param_with_no_bypass_call_still_fails_the_bland_test():
    """The realistic bare build: the model sets amp gain/level and nothing
    else, never calling set_bypass at all. An earlier guard used s.bypass
    alone and missed exactly this case (found in independent review)."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.5},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_LEVEL", "value": -2},
    ]
    scenes = tr.summary_from_plan(plan)
    assert scenes[0].bypass == {}, "fixture must exercise the no-set_bypass-at-all case"
    findings = tr.review(scenes)
    assert [f.rule for f in findings if f.rule == "16"], "a voiced, bare scene must still fail"
    assert not tr.bland_test_passed(findings)


def test_a_whole_build_voiced_only_by_set_param_with_no_bypass_still_fails_missing_eq():
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Clean"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 2.0},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_LEVEL", "value": 1.0},
    ]
    scenes = tr.summary_from_plan(plan)
    assert [f.rule for f in tr.review(scenes) if f.rule == "17"], \
        "a voiced build with no bypass calls at all must still be checked for EQ"


def test_every_catalogued_mix_or_depth_family_is_classified():
    """Completeness gate for FAMILY_CLASS. Independent review found a name-
    pattern-based derivation wrong twice in a row (round 3: too narrow;
    round 4: "any _MIX param" swept in dynamics/EQ blocks and missed a
    _DEPTH-only one). An explicit table cannot silently drift the same way
    a pattern can, but only if nothing catalogued is allowed to be
    missing: a future roster addition must fail HERE, loudly, rather than
    silently falling on one side of either loophole again."""
    catalogued = tr._catalog_mix_or_depth_families()
    assert catalogued, "fixture sanity: the real catalog must yield some families"
    missing = catalogued - set(tr.FAMILY_CLASS)
    assert not missing, f"unclassified catalog families: {sorted(missing)}"
    assert set(tr.FAMILY_CLASS.values()) <= {"audible", "dynamics", "eq", "amp", "boost"}


def test_wet_families_excludes_dynamics_eq_amp_and_boost():
    """The exact round-4 finding: COMP/MULTICOMP/GATE/CROSSOVER/GEQ each
    have a real _MIX parameter but add no TONAL dimension, so none of them
    may satisfy rule 16 on their own. PEQ never appears here at all - it
    has no _MIX/_DEPTH param, so it is reached only through set_bypass."""
    fams = tr.wet_families()
    for dynamics_or_eq in ("COMP", "MULTICOMP", "GATE", "CROSSOVER", "GEQ"):
        assert dynamics_or_eq not in fams, f"{dynamics_or_eq} is not an audible dimension"
    assert "PEQ" not in fams
    assert "DISTORT" not in fams, "the amp block is not an 'effect'"
    assert "FUZZ" not in fams, "boost is tracked separately via boost_gain/boosted"


def test_wet_families_includes_every_real_audible_effect():
    """The other half: legitimate modulation, time-based, pitch, filter/
    spatial, resonator, and synth effects - including ENHANCER, a
    _DEPTH-only family the old _MIX-only derivation missed entirely
    (round 4's second finding) - must all still count."""
    fams = tr.wet_families()
    for audible in ("CHORUS", "FLANGER", "PHASER", "TREMOLO", "ROTARY", "RINGMOD",
                    "DELAY", "MULTITAP", "MEGATAP", "TENTAP", "PLEX",
                    "PITCH", "FORMANT", "FILTER", "REVERB", "RESONATOR",
                    "SYNTH", "VOCODER", "WAH", "ENHANCER"):
        assert audible in fams, f"{audible} is a real audible effect"


def _voice_family_alone(family: str, param_suffix: str) -> list:
    return tr.summary_from_plan([
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": family.lower(),
         "param": f"{family}_{param_suffix}", "value": 40.0},
    ])


_AUDIBLE_MIX_FAMILIES = sorted(f for f, c in tr.FAMILY_CLASS.items() if c == "audible" and f != "ENHANCER")
_DYNAMICS_OR_EQ_MIX_FAMILIES = sorted(f for f, c in tr.FAMILY_CLASS.items() if c in ("dynamics", "eq"))


@pytest.mark.parametrize("family", _AUDIBLE_MIX_FAMILIES)
def test_every_audible_mix_family_prevents_a_false_bland_failure(family):
    """Positive coverage: any real audible effect, voiced ONLY by its own
    _MIX (no set_bypass at all), must not be judged a bare amp+cab."""
    scenes = _voice_family_alone(family, "MIX")
    assert scenes[0].bypass == {}, "fixture must exercise no set_bypass at all"
    assert family in scenes[0].effects, f"{family}_MIX must register as an engaged effect"
    findings = tr.review(scenes)
    assert not [f for f in findings if f.rule == "16"], (
        f"a scene voiced only by {family}_MIX must not be judged bare")


def test_enhancer_depth_alone_prevents_a_false_bland_failure():
    """ENHANCER has only a _DEPTH parameter, no _MIX - the exact family
    round 4 found silently falling out of a _MIX-only derivation."""
    scenes = _voice_family_alone("ENHANCER", "DEPTH")
    assert scenes[0].bypass == {}
    assert "ENHANCER" in scenes[0].effects
    findings = tr.review(scenes)
    assert not [f for f in findings if f.rule == "16"]


@pytest.mark.parametrize("family", _DYNAMICS_OR_EQ_MIX_FAMILIES)
def test_a_dynamics_or_eq_family_alone_does_not_rescue_a_bare_scene(family):
    """Negative coverage, the other half of round 4's finding: a scene
    voiced ONLY by a compressor, gate, crossover, or GEQ - real blocks,
    but none of them a tonal dimension - is still exactly the bare
    amp+cab case rule 16 exists to catch."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_channel", "block": "amp", "value": 1},
        {"kind": "set_bypass", "block": family.lower(), "bypassed": False},
    ]
    scenes = tr.summary_from_plan(plan)
    assert not scenes[0].effects, f"{family} must not register as an 'effect'"
    findings = tr.review(scenes)
    bland = [f for f in findings if f.rule == "16"]
    assert bland and bland[0].severity == "fail", (
        f"a scene voiced only by {family} must still be judged bare")


def test_geq_and_peq_are_equally_not_an_audible_dimension_for_rule_16():
    """The exact asymmetry round 4 flagged: PEQ (no _MIX/_DEPTH param) and
    GEQ (has one) are both classified 'eq' in FAMILY_CLASS and must behave
    IDENTICALLY for rule 16 - neither rescues a bland scene - while both
    still satisfy rule 17 via eq_engaged."""
    for eq_block in ("peq", "geq"):
        plan = [
            {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
            {"kind": "set_scene", "value": 1},
            {"kind": "set_channel", "block": "amp", "value": 1},
            {"kind": "set_bypass", "block": eq_block, "bypassed": False},
        ]
        scenes = tr.summary_from_plan(plan)
        assert scenes[0].eq_engaged is True, f"{eq_block} must still satisfy rule 17"
        bland = [f for f in tr.review(scenes) if f.rule == "16"]
        assert bland and bland[0].severity == "fail", (
            f"{eq_block} alone must not rescue rule 16 either")


def test_fuzz_mix_alone_prevents_the_false_rule_16_failure():
    """Round 5's finding: dialling FUZZ_MIX (no set_bypass, no FUZZ_DRIVE)
    used to reach only `effects` via the old wet_families()-only check -
    FUZZ is classified 'boost', not 'audible', so it never landed in
    effects OR boosted, and a scene that clearly uses its fuzz block
    wrongly hard-failed as bare. One shared classification now means
    FUZZ_MIX registers as a real boost, exactly like FUZZ_DRIVE does."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "fuzz", "param": "FUZZ_MIX", "value": 55},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
    ]
    scenes = tr.summary_from_plan(plan)
    assert scenes[0].bypass == {}, "fixture must exercise no set_bypass at all"
    assert scenes[0].boosted is True, "FUZZ_MIX must register as a real boost"
    findings = tr.review(scenes)
    assert not [f for f in findings if f.rule == "16"], (
        "a scene voiced only by FUZZ_MIX must not be judged bare")


def test_geq_mix_alone_satisfies_rule_17_like_bypass_engagement_does():
    """Round 5's other finding: GEQ_MIX (no set_bypass) never touched
    eq_engaged, so rule 17 fired despite a GEQ value clearly being set.
    Must behave identically to engaging GEQ via set_bypass."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "geq", "param": "GEQ_MIX", "value": 50},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
        {"kind": "set_bypass", "block": "delay", "bypassed": False},
    ]
    scenes = tr.summary_from_plan(plan)
    assert scenes[0].eq_engaged is True, "GEQ_MIX must register as EQ engagement"
    assert not [f for f in tr.review(scenes) if f.rule == "17"]


@pytest.mark.parametrize("family", ("COMP", "MULTICOMP", "GATE", "CROSSOVER"))
def test_a_dynamics_familys_mix_value_still_does_not_satisfy_rule_16(family):
    """The other direction of round 5's fix: unifying the two engagement
    paths must not accidentally let a dynamics family's _MIX value start
    counting as a tonal dimension. A compressor or gate dialled in via
    set_param, with no other effect and no boost, is still bare."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": family.lower(),
         "param": f"{family}_MIX", "value": 60},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
    ]
    scenes = tr.summary_from_plan(plan)
    assert family not in scenes[0].effects
    bland = [f for f in tr.review(scenes) if f.rule == "16"]
    assert bland and bland[0].severity == "fail", (
        f"a scene voiced only by {family}_MIX must still be judged bare")


def test_a_zero_mix_value_does_not_claim_engagement():
    """An explicit 0 is recorded as evidence (fx_mix), but must not count
    as real engagement - a mix at 0 percent is functionally off."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "reverb", "param": "REVERB_MIX", "value": 0},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
    ]
    scenes = tr.summary_from_plan(plan)
    assert scenes[0].fx_mix.get("REVERB") == 0, "the value itself is still recorded"
    assert "REVERB" not in scenes[0].effects, "a 0 mix must not count as engaged"
    bland = [f for f in tr.review(scenes) if f.rule == "16"]
    assert bland and bland[0].severity == "fail"


def test_a_zero_fuzz_drive_does_not_claim_a_boost():
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "fuzz", "param": "FUZZ_DRIVE", "value": 0},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
    ]
    scenes = tr.summary_from_plan(plan)
    assert scenes[0].boost_gain == 0
    assert scenes[0].boosted is False
    bland = [f for f in tr.review(scenes) if f.rule == "16"]
    assert bland and bland[0].severity == "fail"


# --- round 6: add_block is grid-global, not scene-local --------------------

def test_an_added_audible_block_prevents_the_false_rule_16_failure():
    """Round 6's finding: add_block is the documented, correct way to place
    any effect not already on the starter template (chorus, phaser, wah,
    pitch, ...), and summary_from_plan had no branch for it at all - a
    scene voiced purely by adding a real block read as bare amp+cab."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_LEVEL", "value": 0.0},
        {"kind": "add_block", "block": "chorus", "instance": 1, "position": "post"},
    ]
    scenes = tr.summary_from_plan(plan)
    assert "CHORUS" in scenes[0].effects
    findings = tr.review(scenes)
    assert not [f for f in findings if f.rule == "16"], (
        "a scene voiced by adding a real effect block must not be judged bare")
    assert tr.bland_test_passed(findings)


def test_an_added_dynamics_or_boost_block_is_classified_the_same_via_add_block():
    """add_block must go through the SAME FAMILY_CLASS classification as
    every other path: a dynamics block (gate) still does not satisfy rule
    16 on its own, and a boost block (fuzz) satisfies boosted, not effects."""
    dynamics_plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
        {"kind": "add_block", "block": "gate", "instance": 1, "position": "pre"},
    ]
    scenes = tr.summary_from_plan(dynamics_plan)
    assert "GATE" not in scenes[0].effects
    bland = [f for f in tr.review(scenes) if f.rule == "16"]
    assert bland and bland[0].severity == "fail"

    boost_plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
        {"kind": "add_block", "block": "fuzz", "instance": 1, "position": "pre"},
    ]
    scenes = tr.summary_from_plan(boost_plan)
    assert scenes[0].boosted is True
    assert "FUZZ" not in scenes[0].effects
    assert not [f for f in tr.review(scenes) if f.rule == "16"]

    eq_plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "amp", "param": "DISTORT_DRIVE", "value": 6.0},
        {"kind": "add_block", "block": "geq", "instance": 1, "position": "post"},
    ]
    scenes = tr.summary_from_plan(eq_plan)
    assert scenes[0].eq_engaged is True
    assert not [f for f in tr.review(scenes) if f.rule == "17"]


def test_a_globally_added_block_alone_does_not_falsely_differentiate_scenes():
    """The FM9 invariant round 6 flagged: add_block places a block on the
    shared grid, not into one scene. Two scenes that both simply inherit
    the same globally-added block, with no scene-specific bypass/channel
    difference at all, are still the same sound under two names - the
    global block's presence alone must not manufacture a clone-detection
    escape."""
    plan = (_scene_actions(2, "A", 2, 1, False)
            + _scene_actions(4, "B", 2, 1, False)
            + [{"kind": "add_block", "block": "chorus", "instance": 1, "position": "post"}])
    scenes = tr.summary_from_plan(plan)
    clone = [f for f in tr.review(scenes) if f.rule == "15"]
    assert clone, "a shared global block must not hide an otherwise-real clone"


def test_an_explicit_per_scene_bypass_difference_still_differentiates_scenes():
    """The other half: once a scene EXPLICITLY overrides the globally-added
    block's default (bypasses it for just that scene), that scene-specific
    fact must still correctly tell the two scenes apart."""
    plan = (_scene_actions(2, "A", 2, 1, False)
            + _scene_actions(4, "B", 2, 1, False)
            + [{"kind": "add_block", "block": "chorus", "instance": 1, "position": "post"},
               {"kind": "set_scene", "value": 4},
               {"kind": "set_bypass", "block": "chorus", "bypassed": True}])
    scenes = tr.summary_from_plan(plan)
    by_n = {s.n: s for s in scenes}
    assert "CHORUS" in by_n[2].effects, "scene 2 keeps the shared default (not bypassed)"
    assert "CHORUS" not in by_n[4].effects, "scene 4's explicit bypass overrides the default"
    clone = [f for f in tr.review(scenes) if f.rule == "15"]
    assert not clone, "the explicit per-scene bypass difference must tell them apart"


def test_scene_level_alone_is_not_tone_voicing():
    """Explicit decision (independent review round 3): OUTPUT_SCENEn is an
    output-level TRIM, the same category of fact as a channel assignment -
    it says how loud a scene is relative to the others, nothing about what
    it sounds like. A plan that only balances scene volume must still read
    as unvoiced, exactly like the set_channel-only case."""
    plan = [
        {"kind": "rename_scene", "value": 2, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 2},
        {"kind": "set_param", "block": "output", "param": "OUTPUT_SCENE2", "value": -1.5},
    ]
    scenes = tr.summary_from_plan(plan)
    assert scenes[0].scene_level == -1.5, "fixture must actually set scene_level"
    assert scenes[0].amp_gain is None and scenes[0].amp_level is None
    assert not tr._voiced(scenes[0]), "scene_level alone must not count as voiced"
    findings = tr.review(scenes)
    assert not [f for f in findings if f.rule in ("16", "17")]


def test_a_scene_the_plan_only_reassigns_a_channel_on_is_still_not_bland_or_missing_eq():
    """The other side of the same fix: a bare set_channel reassignment (no
    gain/level/bypass at all) still must not be judged - this is the
    coverage() fixture from #54, preserved."""
    plan = [
        {"kind": "rename_scene", "value": 4, "type_name": "Lead"},
        {"kind": "set_scene", "value": 4},
        {"kind": "set_channel", "block": "amp", "value": 2},
    ]
    scenes = tr.summary_from_plan(plan)
    assert not [f for f in tr.review(scenes) if f.rule in ("16", "17")]


def test_a_scene_with_an_effect_or_a_boost_does_not_fail_the_bland_test():
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_channel", "block": "amp", "value": 1},
        {"kind": "set_bypass", "block": "delay", "bypassed": False},
    ]
    scenes = tr.summary_from_plan(plan)
    findings = tr.review(scenes)
    assert not [f for f in findings if f.rule == "16"]
    assert tr.bland_test_passed(findings)


def test_a_boost_dialled_without_a_matching_set_bypass_call_is_not_bland():
    """Found in independent review: a donor/inherited channel can already
    have its drive engaged, with the plan only touching FUZZ_DRIVE's gain -
    no set_bypass repeated. That must count as a real boost, not silently
    read as 'no boost' just because bypass was not re-stated."""
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Rhythm"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "drive", "param": "FUZZ_DRIVE", "value": 6.0},
    ]
    scenes = tr.summary_from_plan(plan)
    assert scenes[0].bypass == {}, "fixture must exercise no set_bypass at all"
    assert scenes[0].boosted is True
    findings = tr.review(scenes)
    assert not [f for f in findings if f.rule == "16"]
    assert tr.bland_test_passed(findings)


def test_an_effect_mix_dialled_without_a_matching_set_bypass_call_is_not_bland():
    plan = [
        {"kind": "rename_scene", "value": 1, "type_name": "Clean"},
        {"kind": "set_scene", "value": 1},
        {"kind": "set_param", "block": "reverb", "param": "REVERB_MIX", "value": 35.0},
    ]
    scenes = tr.summary_from_plan(plan)
    assert scenes[0].bypass == {}, "fixture must exercise no set_bypass at all"
    assert "REVERB" in scenes[0].effects
    findings = tr.review(scenes)
    assert not [f for f in findings if f.rule == "16"]


def test_an_untouched_scene_is_not_accused_of_being_bland():
    """A scene the plan says nothing structural about (empty shape) cannot be
    judged bare - that would fire on every delta plan that only renames a
    scene, the same guard clones() already uses."""
    plan = [{"kind": "rename_scene", "value": 1, "type_name": "Clean"}]
    scenes = tr.summary_from_plan(plan)
    assert not [f for f in tr.review(scenes) if f.rule == "16"]


# --- issue #97: every build leaves an EQ fine-tune handle -------------------

def test_build_with_no_eq_block_anywhere_fails_review():
    """WARN, not fail: several professional reference presets
    (test_tone_targets.py) gig fine with no EQ block at all, the same
    empirical pattern rule 10's margin follows (issue #65)."""
    plan = (_scene_actions(1, "Clean", 0, 0, False)
            + _scene_actions(2, "Rhythm", 1, 0, True))
    scenes = tr.summary_from_plan(plan)
    findings = tr.review(scenes)
    eq_findings = [f for f in findings if f.rule == "17"]
    assert len(eq_findings) == 1
    assert eq_findings[0].severity == "warn"


def test_an_engaged_peq_anywhere_in_the_build_satisfies_the_eq_rule():
    plan = (_scene_actions(1, "Clean", 0, 0, False)
            + [{"kind": "set_scene", "value": 1},
               {"kind": "set_bypass", "block": "peq", "bypassed": False}])
    scenes = tr.summary_from_plan(plan)
    assert not [f for f in tr.review(scenes) if f.rule == "17"]


def test_an_engaged_geq_anywhere_in_the_build_satisfies_the_eq_rule():
    plan = (_scene_actions(1, "Clean", 0, 0, False)
            + [{"kind": "set_scene", "value": 1},
               {"kind": "set_bypass", "block": "geq", "bypassed": False}])
    scenes = tr.summary_from_plan(plan)
    assert not [f for f in tr.review(scenes) if f.rule == "17"]


def test_a_plan_touching_nothing_structural_is_not_accused_of_missing_eq():
    plan = [{"kind": "rename_scene", "value": 1, "type_name": "Clean"}]
    scenes = tr.summary_from_plan(plan)
    assert not [f for f in tr.review(scenes) if f.rule == "17"]
