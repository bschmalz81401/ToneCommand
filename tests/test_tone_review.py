"""The deterministic pre-ship tone check (fm9/tone_review.py): the enforcement
half of config/tone_rules.md rule 14. It must catch the failures that actually
shipped on 2026-09-04 (cleans cut quiet and dry, leads that do not out-saturate
the rhythm) and stay quiet on a build that is done right.
"""
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
