"""The committed HeadRush schema, and the generator that produced it (#33 phase 2).

Nothing here touches the network. The generator's reshape is a pure function so
it can be driven from small synthetic trees, and the committed artifact is read
off disk, which is the same split phase 1 used for the same reason: a snapshot
of someone else's hardware is only reviewable if the review needs none.

Tests are named after what would go wrong. The expensive failure this phase can
have is not a crash, it is a file that looks complete and is not, or one that
carries the owner's rig state into a public repo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.build_headrush_schema import (  # noqa: E402
    ALLOWLIST,
    FIRMWARE_PATH,
    FIRMWARE_PROPERTY,
    Refused,
    _meta_hash,
    build,
    main,
)

ARTIFACT = ROOT / "config" / "headrush_schema.json"


@pytest.fixture(scope="module")
def artifact():
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _tree(**overrides):
    """A minimal tree with everything build() requires, for negative tests."""
    tree = {
        FIRMWARE_PATH: {"meta": {"type": "object"}, "value": {FIRMWARE_PROPERTY: "9.9.9.test"}},
        "/Evil/API/Blocks": {
            "meta": {"type": "object"},
            "value": {
                "ModuleTypes": ["Empty Slot", "Amp"],
                "BlockSelectorCategories": ["Amp"],
                "BlockSelectorCategoriesForDisplay": ["Amp"],
            },
        },
        "/Evil/Engine/Patch/Amp": {"meta": {"properties": {"Gain": {"type": "number"}}}, "value": {"Gain": 0.73}},
    }
    tree.update(overrides)
    return tree


# --- the generator refuses rather than guessing ---------------------------

def test_a_snapshot_with_no_firmware_is_refused_rather_than_written():
    """Every fact in the file is true of ONE firmware. #33 already agreed to
    treat Prime and Flex Prime as untested rather than assumed, and an
    unlabelled snapshot silently destroys the ability to say that.
    """
    tree = _tree()
    tree[FIRMWARE_PATH] = {"meta": {}, "value": {}}
    with pytest.raises(Refused, match="firmware"):
        build(tree)


@pytest.mark.parametrize("bad", [None, "", "   ", 5, [], {}])
def test_a_firmware_that_is_not_a_real_string_is_refused(bad):
    """A blank or non-string version would be written into the file verbatim and
    read later as though somebody had checked it."""
    tree = _tree()
    tree[FIRMWARE_PATH] = {"meta": {}, "value": {FIRMWARE_PROPERTY: bad}}
    with pytest.raises(Refused):
        build(tree)


def test_a_missing_model_roster_is_refused_rather_than_omitted():
    """This is the expensive one. Chain.ModuleType{n} is a bare integer 0..277
    with no names of its own, so without the roster the file still looks like a
    complete schema while having lost the only thing that says what a slot
    holds. Everything downstream would treat it as complete.
    """
    tree = _tree()
    del tree["/Evil/API/Blocks"]["value"]["ModuleTypes"]
    with pytest.raises(Refused, match="ModuleTypes"):
        build(tree)


@pytest.mark.parametrize("bad", [[], "Empty Slot", {}, None, 0])
def test_a_roster_of_the_wrong_shape_is_refused(bad):
    """An empty list is what a half-failed fetch looks like, and it would be
    committed as a device that can hold nothing."""
    tree = _tree()
    tree["/Evil/API/Blocks"]["value"]["ModuleTypes"] = bad
    with pytest.raises(Refused):
        build(tree)


def test_an_absent_allowlisted_object_is_refused():
    tree = _tree()
    del tree["/Evil/API/Blocks"]
    with pytest.raises(Refused, match="/Evil/API/Blocks"):
        build(tree)


@pytest.mark.parametrize("tree", [{}, None, [], "not a tree"])
def test_an_empty_or_wrongly_shaped_response_is_refused(tree):
    with pytest.raises(Refused):
        build(tree)


def test_an_object_without_meta_is_refused_because_the_response_is_not_that_shape():
    tree = _tree()
    tree["/Evil/Engine/Patch/Amp"] = {"value": {"Gain": 1.0}}
    with pytest.raises(Refused, match="meta"):
        build(tree)


@pytest.mark.parametrize("bad", [[], ["AppVersion"], 5, "text", None])
def test_a_firmware_object_whose_value_is_not_an_object_is_refused(bad):
    """The same hole as the test below, on the object read thirty lines earlier.

    The first round of fixes closed it in the allowlist loop and left it in the
    firmware read, and neither the comment nor the test written for it noticed,
    because both mutate /Evil/API/Blocks. The allowlist loop would have caught
    this node eventually, since FIRMWARE_PATH is allowlisted, but the firmware
    is extracted before that loop runs.
    """
    tree = _tree()
    tree[FIRMWARE_PATH]["value"] = bad
    with pytest.raises(Refused):
        build(tree)


@pytest.mark.parametrize("bad", [[], ["ModuleTypes"], 5, "text", None])
def test_an_allowlisted_object_whose_value_is_not_an_object_is_refused(bad):
    """`node.get("value") or {}` turned a list or an int into a TypeError on the
    membership test one line later. main() catches only Refused, so the operator
    got a traceback instead of the "nothing written" that tells them the
    snapshot is not on disk.
    """
    tree = _tree()
    tree["/Evil/API/Blocks"]["value"] = bad
    with pytest.raises(Refused):
        build(tree)


@pytest.mark.parametrize("bad", [[1, 2, 3], ["Empty Slot", ""], ["Empty Slot", None], [["nested"]], ["  "]])
def test_a_roster_holding_things_that_are_not_names_is_refused(bad):
    """Checking the container and not its contents let a list of integers
    through. ModuleTypes is what Chain.ModuleType{n} indexes into, so that is
    the "looks complete, slot names are gone" failure with extra steps.
    """
    tree = _tree()
    tree["/Evil/API/Blocks"]["value"]["ModuleTypes"] = bad
    with pytest.raises(Refused, match="non-empty strings"):
        build(tree)


def test_a_hash_collision_is_refused_rather_than_silently_deduped(monkeypatch):
    """setdefault would keep the first meta and file a second, different one
    under the same key, which is precisely how a lossless dedup stops being
    lossless. 64 bits across 161 items makes this vanishingly unlikely; it is
    checked anyway, because the file's own notes claim losslessness.
    """
    import tools.build_headrush_schema as gen
    monkeypatch.setattr(gen, "_meta_hash", lambda meta: "collide")
    tree = _tree()
    tree["/a"] = {"meta": {"properties": {"X": {"type": "number"}}}, "value": {}}
    tree["/b"] = {"meta": {"properties": {"Y": {"type": "number"}}}, "value": {}}
    with pytest.raises(Refused, match="collision"):
        gen.build(tree)


# --- the command line contract --------------------------------------------

def test_a_refused_run_exits_non_zero_and_leaves_no_file(tmp_path, capsys):
    """"Refuses rather than guesses" is only true if the caller can tell. A
    generator that returned 0 and wrote nothing would be indistinguishable from
    success in any script that runs it.
    """
    bad = tmp_path / "raw.json"
    tree = _tree()
    del tree["/Evil/API/Blocks"]["value"]["ModuleTypes"]
    bad.write_text(json.dumps(tree), encoding="utf-8")
    out = tmp_path / "schema.json"

    assert main(["--from-file", str(bad), "--out", str(out)]) == 1
    assert not out.exists()
    assert "REFUSED" in capsys.readouterr().err


def test_a_run_that_succeeds_writes_the_file_and_exits_zero(tmp_path):
    good = tmp_path / "raw.json"
    good.write_text(json.dumps(_tree()), encoding="utf-8")
    out = tmp_path / "schema.json"

    assert main(["--from-file", str(good), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["device"] == "headrush"


def test_save_raw_happens_only_after_the_build_succeeds_and_says_what_it_wrote(tmp_path, capsys):
    """The raw response is the whole subtree, live values included, which on the
    real capture meant 9055 property values, the owner's rig names and their
    setlist names. It used to be written BEFORE build(), so a refused run
    dropped a file full of preset data and then printed "nothing written",
    which was false about the one file that most needed saying.
    """
    tree = _tree()
    del tree["/Evil/API/Blocks"]["value"]["ModuleTypes"]
    bad = tmp_path / "raw.json"
    bad.write_text(json.dumps(tree), encoding="utf-8")
    raw_out = tmp_path / "saved.json"

    assert main(["--from-file", str(bad), "--out", str(tmp_path / "s.json"),
                 "--save-raw", str(raw_out)]) == 1
    assert not raw_out.exists(), "a refused run wrote the owner's live values anyway"

    good = tmp_path / "ok.json"
    good.write_text(json.dumps(_tree()), encoding="utf-8")
    assert main(["--from-file", str(good), "--out", str(tmp_path / "s2.json"),
                 "--save-raw", str(raw_out)]) == 0
    assert raw_out.exists()
    err = capsys.readouterr().err
    assert "Do not commit it" in err and "LIVE VALUES" in err


# --- what must not be in the file -----------------------------------------

def test_no_property_value_is_committed_except_the_allowlist():
    """The subtree response carries the owner's live rig state beside the
    schema: amp gain, which cab is loaded, the current rig's fourteen slots.
    AGENTS.md says not to commit preset payloads, and a snapshot that quietly
    included them would put someone's rig in a public repo belonging to someone
    else. Measured on the real capture: 9055 property values present, 4 kept.
    """
    out = build(_tree())
    committed = {(p, n) for p, ws in out["rosters"].items() for n in ws}
    allowed = {(p, n) for p, ws in ALLOWLIST.items() for n in ws}
    assert committed == allowed
    assert "0.73" not in json.dumps(out), "an owner's parameter value reached the artifact"


def test_the_committed_artifact_carries_no_live_values(artifact):
    """The guard above proves the generator's intent; this proves the file that
    actually shipped."""
    committed = {(p, n) for p, ws in artifact["rosters"].items() for n in ws}
    assert committed == {(p, n) for p, ws in ALLOWLIST.items() for n in ws}

    def has_value_key(node):
        if isinstance(node, dict):
            return "value" in node or any(has_value_key(v) for v in node.values())
        if isinstance(node, list):
            return any(has_value_key(v) for v in node)
        return False

    assert not has_value_key(artifact["metas"])


# --- the dedup must be lossless -------------------------------------------

def test_metas_are_stored_once_and_every_path_resolves(artifact):
    """A path pointing at a hash that is not in `metas` is a file that looks
    whole and cannot be read back."""
    missing = [p for p, h in artifact["paths"].items() if h not in artifact["metas"]]
    assert missing == []
    assert len(artifact["paths"]) == artifact["object_count"]
    assert len(artifact["metas"]) == artifact["distinct_meta_count"]


def test_the_committed_snapshot_is_the_whole_tree_and_not_a_slice_of_it():
    """Self-consistency is not completeness, and this is the gap that matters.

    Every other check here asks whether the file agrees with itself. A file
    holding 200 objects, with object_count 200 and every path resolving, passes
    all of them. The roster, the fourteen slots, the ten routings and the
    Greener sharing are a fingerprint of a handful of objects, not of the other
    ~290, so a fetch that dropped a slice of effects or cabs would still look
    like a complete schema and phases 3 and 4 would be built from it with a
    green gate. That is the expensive failure this phase's own module docstring
    names, and nothing was pinning it.

    Measured on a HeadRush Core at fw 5.1.0.2a63755. If a firmware genuinely
    changes these, this test is where that gets noticed and argued about
    deliberately, which is the point of committing the snapshot at all.
    """
    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert art["object_count"] == 309
    assert art["distinct_meta_count"] == 161
    assert len(art["paths"]) == 309
    assert len(art["metas"]) == 161


def test_the_artifact_declares_the_shape_consumers_read_it_by(artifact):
    """phase 3 builds a simulator from this file. A key quietly renamed or
    dropped breaks that with no other signal."""
    assert artifact["schema_version"] == 1
    assert artifact["device"] == "headrush"
    assert set(artifact) == {
        "schema_version", "device", "firmware", "support_status", "source",
        "generated_by", "content", "keyed_by", "warning", "notes",
        "object_count", "distinct_meta_count", "shared_meta_groups",
        "rosters", "paths", "metas",
    }


def test_every_stored_meta_actually_hashes_to_the_key_it_is_filed_under(artifact):
    """The dedup is only lossless if the key is derived from the content. A
    hand edit to a meta, or a generator that filed one under the wrong key,
    shows up here and nowhere else.
    """
    wrong = [h for h, meta in artifact["metas"].items() if _meta_hash(meta) != h]
    assert wrong == []


def test_two_objects_with_the_same_meta_share_one_entry():
    tree = _tree()
    # Deliberately unlike anything else in the fixture, or the group also picks
    # up the fixture's own Amp and the assertion below is about the wrong thing.
    same = {"properties": {"OnlyHere": {"type": "number"}}}
    tree["/a"] = {"meta": same, "value": {}}
    tree["/b"] = {"meta": json.loads(json.dumps(same)), "value": {}}
    out = build(tree)
    assert out["paths"]["/a"] == out["paths"]["/b"]
    assert ["/a", "/b"] in out["shared_meta_groups"]


def test_sharing_is_reported_only_when_it_is_real():
    tree = _tree()
    tree["/a"] = {"meta": {"properties": {"X": {"type": "number"}}}, "value": {}}
    tree["/b"] = {"meta": {"properties": {"Y": {"type": "number"}}}, "value": {}}
    out = build(tree)
    assert out["paths"]["/a"] != out["paths"]["/b"]
    assert not any("/a" in g and "/b" in g for g in out["shared_meta_groups"])


def test_the_generator_is_byte_for_byte_deterministic():
    """A snapshot that reorders itself, or stamps the clock into itself, makes
    the next firmware's diff unreadable, which is the only reason to commit it
    rather than fetch it. No existing config artifact carries a capture time
    and this one does not either: re-running on the same firmware must be a
    no-op diff, or nobody will re-run it.
    """
    assert json.dumps(build(_tree()), sort_keys=True) == json.dumps(build(_tree()), sort_keys=True)


def test_the_committed_artifact_stamps_no_clock(artifact):
    """Two independent fetches from the unit, three minutes apart, produced
    identical files. A timestamp would have made that invisible."""
    assert "captured_utc" not in artifact
    assert artifact["generated_by"] == "tools/build_headrush_schema.py"


# --- what the unit actually said, pinned ----------------------------------

def test_the_firmware_is_the_one_every_other_fact_here_was_measured_on(artifact):
    """#33 committed to stating the tested firmware rather than implying
    coverage. If this changes, every measurement in #109 and #116 is about a
    different unit state and has to be re-read, not assumed."""
    assert artifact["firmware"] == "5.1.0.2a63755"
    assert "UNTESTED" in artifact["support_status"]


def test_the_firmware_is_not_two_different_facts(artifact):
    """`firmware` and the allowlisted AppVersion are the same reading. Only the
    former was pinned, so they could drift and the file would name two."""
    assert artifact["firmware"] == artifact["rosters"]["/Evil/Gui"]["AppVersion"]


def test_the_block_category_vocabularies_are_the_sizes_the_changelog_names(artifact):
    """Committed as prose in the CHANGELOG with nothing checking them."""
    rosters = artifact["rosters"]["/Evil/API/Blocks"]
    assert len(rosters["BlockSelectorCategories"]) == 21
    assert len(rosters["BlockSelectorCategoriesForDisplay"]) == 20


def test_the_block_roster_is_the_278_entries_109_measured(artifact):
    """Chain.ModuleType{n} is an index into this and nothing else."""
    roster = artifact["rosters"]["/Evil/API/Blocks"]["ModuleTypes"]
    assert len(roster) == 278
    assert roster[0] == "Empty Slot", "index 0 is the empty slot, not a model"


def test_a_chain_slot_names_no_models_of_its_own(artifact):
    """The thing that makes the roster load bearing: the slot property is a bare
    integer. Read it without the roster and a preset is a list of numbers."""
    chain = artifact["metas"][artifact["paths"]["/Evil/Engine/Patch/Chain"]]["properties"]
    slot = chain["ModuleType1"]
    assert slot["type"] == "integer"
    assert (slot["minimum"], slot["maximum"]) == (0.0, 277.0)
    assert "x-options" not in slot
    assert len(artifact["rosters"]["/Evil/API/Blocks"]["ModuleTypes"]) == int(slot["maximum"]) + 1


def test_the_chain_is_fourteen_slots(artifact):
    """Fourteen linear slots is the shape the whole Topology.SELECTED argument
    on #109 rests on."""
    chain = artifact["metas"][artifact["paths"]["/Evil/Engine/Patch/Chain"]]["properties"]
    slots = sorted(int(k[len("ModuleType"):]) for k in chain
                   if k.startswith("ModuleType") and k[len("ModuleType"):].isdigit())
    assert slots == list(range(1, 15))


def test_the_ten_routings_are_published_as_names_and_nothing_more(artifact):
    """The line #109 drew for Topology.SELECTED: the unit publishes an integer,
    its range and ten strings. It never publishes what a routing is SHAPED
    like, which is why the slot-to-branch table has to be adapter knowledge.
    """
    chain = artifact["metas"][artifact["paths"]["/Evil/Engine/Patch/Chain"]]["properties"]
    routing = chain["Routing"]
    assert routing["type"] == "integer"
    assert (routing["minimum"], routing["maximum"]) == (0.0, 9.0)
    assert routing["x-options"]["strings"] == [
        "S", "SPS-1", "SPS-2", "SPS-3", "PS-1",
        "Vocal", "Dual", "Dual Vox-4", "Dual Vox-2", "DualGuit",
    ]
    assert set(routing) <= {"type", "minimum", "maximum", "x-options"}, \
        "a field describing a routing's shape would change the SELECTED argument"


def test_no_committed_meta_carries_a_twin_field(artifact):
    """#109 says the _2 objects carry `twin: true`. No committed meta does.

    WHAT THIS TEST CHECKS, precisely, because an earlier version of this
    docstring claimed more. It walks the metas in the committed file, which are
    the `subtree` response after reshaping. It does NOT fetch anything, and it
    does not compare `/api/v1/object-meta` against `/api/v1/subtree`; that
    comparison was made by hand against the unit on fw 5.1.0.2a63755, came back
    byte-identical, and lives in the artifact's own notes labelled as not
    checked by the generator. Recording an off-CI measurement as though CI
    proved it is the class of confident overstatement this project has already
    rejected a change for.

    The string replace is not a way of passing: `"twin": true` survives it. It
    exempts the single measured hit, a cabinet named "California Twin 212
    Combo", so the assertion can be about a field rather than a substring.
    """
    for path, digest in artifact["paths"].items():
        blob = json.dumps(artifact["metas"][digest]).lower()
        assert "twin" not in blob.replace("california twin 212 combo", ""), path


def test_identical_parameter_surfaces_are_not_only_twins(artifact):
    """The finding that makes content-hash dedup the right shape, and a caution
    for phase 4: a parameter set is NOT an identity for a model. Ten groups are
    not base/base_2 pairs, so a consumer matching blocks by their schema would
    conflate genuinely different models.
    """
    not_twin_pairs = [
        g for g in artifact["shared_meta_groups"]
        if not (len(g) == 2 and g[1] == g[0] + "_2")
    ]
    assert len(not_twin_pairs) == 10
    flat = {p for g in not_twin_pairs for p in g}
    # Every pair the artifact's notes and the CHANGELOG name out loud. Pinning
    # only the first left the other two as prose nobody would notice going
    # stale.
    for named in ("/Evil/Engine/Patch/Greener", "/Evil/Engine/Patch/Green_JRC-OD",
                  "/Evil/Engine/Patch/Black_Wah", "/Evil/Engine/Patch/Shine_Wah",
                  "/Evil/Engine/Patch/Amp_Clone", "/Evil/Engine/Patch/Pedal_Clone"):
        assert named in flat, named


# --- house rules -----------------------------------------------------------

def test_no_em_dash_in_the_files_this_phase_touched():
    em_dash = chr(0x2014)
    for rel in ("tools/build_headrush_schema.py", "tests/test_headrush_schema.py"):
        assert em_dash not in (ROOT / rel).read_text(encoding="utf-8"), f"em dash in {rel}"


def test_the_changelog_records_this_phase():
    assert "config/headrush_schema.json" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
