"""The HeadRush block and parameter registry.

#33 phase 3, #122. One section per acceptance criterion, plus the refusals
that are the reason the registry is worth having rather than a convenience.

No unit and no network: the input is the committed schema.
"""
import json

import pytest

from devices.headrush import registry as R
from tools import build_headrush_registry as gen


@pytest.fixture(scope="module")
def schema():
    return json.loads((R.SCHEMA).read_text())


@pytest.fixture(scope="module")
def reg():
    return R.load()


# --- AC1: generation is deterministic from the committed schema ---------

def test_two_runs_on_one_schema_agree(schema):
    assert gen.build(schema) == gen.build(schema)


def test_the_committed_file_is_what_the_generator_produces(schema):
    """Not "it parses": byte equality with a fresh build. A registry that had
    drifted from its generator would be hand edited data wearing a
    `generated_by` field."""
    fresh = json.dumps(gen.build(schema), indent=2, sort_keys=True) + "\n"
    assert R.REGISTRY.read_text() == fresh


def test_no_hardware_is_reachable_from_the_generator():
    """The ticket says hardware is not required, so nothing here may import a
    transport. Checked rather than asserted in a docstring."""
    import inspect
    source = inspect.getsource(gen)
    assert "client" not in source.replace("HeadrushClient", "")
    assert "urllib" not in source and "socket" not in source


# --- AC2: continuous properties keep device-published units and ranges --

def test_a_continuous_parameter_carries_the_devices_range_and_unit(reg):
    bass = reg.resolve("Amp", "Bass")
    assert bass.kind == "continuous"
    assert (bass.display_minimum, bass.display_maximum) == (0.0, 100.0)
    assert bass.display_format == "%.0f %%"
    assert bass.unit == "%"


def test_units_are_read_off_the_devices_own_format_string(reg):
    assert reg.resolve("Amp", "PostGain").unit == "dB"
    assert reg.resolve("Amp", "TremSpeed").unit == "Hz"
    assert reg.resolve("Amp", "MidFreq").unit == "Hz"
    assert reg.resolve("Amp", "Width").unit is None, "'%.0f' carries no unit"


def test_the_devices_inconsistent_spelling_is_preserved_not_tidied():
    """The firmware writes both 'dB' and 'db'. Normalising would put this
    repo's opinion where the device's own string belongs, and a caller
    comparing against a unit it read from a unit would then miss."""
    assert gen.unit_of("%.1f db") == "db"
    assert gen.unit_of("%.1f dB") == "dB"
    assert gen.unit_of("% .1f dB") == "dB", "a space flag is still a conversion"
    assert gen.unit_of("%.0f %%") == "%"
    assert gen.unit_of("%.1f") is None


def test_an_unparseable_format_refuses_rather_than_inventing_a_unit():
    with pytest.raises(SystemExit, match="no printf conversion"):
        gen.unit_of("just words")


def test_wire_range_and_display_range_are_kept_apart(reg):
    """The measurement this whole file rests on. Amp.Bass shows 0..100 and
    takes 0..1, and the device accepts a write of 75 without complaint, so
    nothing catches a caller that conflated them except this split."""
    bass = reg.resolve("Amp", "Bass")
    assert bass.wire_range == (0.0, 1.0)
    assert (bass.display_minimum, bass.display_maximum) == (0.0, 100.0)
    assert reg.wire_encoding["continuous"] == "normalised 0..1"
    assert reg.wire_encoding["conversion_to_display"] is None
    # what was measured and what was inferred from it are stated separately
    assert "Amp.Bass" in reg.wire_encoding["measured_on"]
    assert "3912" in reg.wire_encoding["generalised_by"]


def test_defaults_are_recorded_as_normalised(reg):
    """Every published default sits in 0..1 while ranges span display units,
    which is the schema's own corroboration of the hardware reading. SltEQHP
    defaults to 0.0 on a 25..1000 Hz range: as a display value that is below
    its own published minimum, as a normalised one it is a high pass off."""
    hp = reg.resolve("Amp", "SltEQHP")
    assert hp.default_normalised == 0.0
    assert hp.display_minimum == 25.0
    lp = reg.resolve("Amp", "SltEQLP")
    assert lp.default_normalised == 1.0 and lp.display_maximum == 16000.0

    everything = [p.default_normalised
                  for b in reg.blocks.values() for p in b.parameters.values()
                  if p.kind == "continuous" and p.default_normalised is not None]
    assert everything and all(0.0 <= d <= 1.0 for d in everything)


def test_the_generalisation_off_one_block_is_counted_not_assumed(reg):
    """The hardware reading covers Amp and the registry describes 302 objects,
    so the step between them is the weak point and is measured rather than
    waved at. 1369 defaults lie OUTSIDE their own published display range,
    which no amount of coincidence explains: they cannot be display values."""
    total = in01 = impossible = 0
    objects = set()
    for path, block in reg.blocks.items():
        for param in block.parameters.values():
            default = param.default_normalised
            if param.kind != "continuous" or default is None:
                continue
            total += 1
            objects.add(path)
            in01 += 0.0 <= default <= 1.0
            lo, hi = param.display_minimum, param.display_maximum
            if lo is not None and hi is not None and not lo <= default <= hi:
                impossible += 1
    assert (total, in01) == (3912, 3912), "every published default is in 0..1"
    assert impossible == 1369, "and a third of them cannot be display values"
    assert len(objects) == 290, "spanning almost every object, not just Amp"


# --- AC3: selectors keep device-published options -----------------------

def test_a_selector_carries_the_devices_own_option_names(reg):
    amp_type = reg.resolve("Amp", "Type")
    assert amp_type.kind == "selector"
    assert len(amp_type.options) == 53
    assert amp_type.option(0) == "59 Deluxe Gain Mod"


def test_selector_options_match_the_schema_exactly(reg, schema):
    """Copied, not re-derived: the registry must not have reordered or
    deduplicated what the device published."""
    meta = schema["metas"][schema["paths"]["/Evil/Engine/Patch/Amp"]]
    published = meta["properties"]["Type"]["x-options"]["strings"]
    assert reg.resolve("Amp", "Type").options == published


def test_an_integer_with_no_published_names_is_not_given_any(reg):
    """Amp.Colour spans -1..13 and the device names none of the positions.
    Inventing names is the failure this kind exists to make impossible."""
    colour = reg.resolve("Amp", "Colour")
    assert colour.kind == "ordinal"
    assert colour.options is None
    with pytest.raises(R.NotMeasured):
        colour.option(0)


def test_a_switch_carries_its_two_labels_where_the_device_gives_them(reg):
    stereo = reg.resolve("Chain", "RigIsStereo")
    assert stereo.kind == "switch"
    assert stereo.options == ["Mono", "Stereo"]


def test_presentation_arrays_are_not_parameters(reg):
    """order, labels and disabled are live per-model state saying which
    controls the unit draws. A caller iterating parameters must not take them
    for knobs."""
    for name in ("order", "labels", "disabled"):
        assert reg.resolve("Amp", name).kind == "presentation"


# --- AC4: no FM9 equivalence is guessed ---------------------------------

def test_nothing_in_the_registry_mentions_the_other_device():
    """Both rosters have a block with a knob called Bass and the two are not
    interchangeable. The absence is the criterion, so it is checked over the
    whole file rather than argued in a docstring."""
    blob = json.loads(R.REGISTRY.read_text())
    payload = json.dumps(blob["blocks"]).lower()
    for token in ("fm9", "fractal", "axe-fx", "effect_id", "cc#"):
        assert token not in payload
    # and the envelope says so in words, rather than merely happening not to
    assert "no FM9 equivalence is recorded" in blob["warning"]


def test_no_block_claims_a_category_it_was_not_told(reg):
    """The device answers categories by method, not in the schema. Absent
    rather than inferred from the block's name."""
    assert all(b.category is None for b in reg.blocks.values())


# --- AC5: schema drift fails loudly with a useful diagnostic ------------

def test_a_firmware_bump_is_named_as_one(tmp_path, schema):
    moved = dict(schema, firmware="9.9.9.deadbeef")
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(moved))
    R.load.cache_clear()
    with pytest.raises(R.SchemaDrift) as err:
        R.load(schema=path)
    text = str(err.value)
    assert "5.1.0.2a63755" in text and "9.9.9.deadbeef" in text
    assert "build_headrush_registry" in text, "say what to run"


def test_an_edited_schema_at_the_same_firmware_is_named_differently(tmp_path, schema):
    """The two causes want opposite responses, so one message for both would
    send the reader the wrong way."""
    edited = json.loads(json.dumps(schema))
    edited["metas"][schema["paths"]["/Evil/Engine/Patch/Amp"]]["properties"]["Bass"]["maximum"] = 11.0
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(edited))
    R.load.cache_clear()
    with pytest.raises(R.SchemaDrift, match="edited rather than regenerated"):
        R.load(schema=path)


def test_the_wrong_device_is_refused(tmp_path, schema):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(dict(schema, device="fm9")))
    R.load.cache_clear()
    with pytest.raises(R.SchemaDrift, match="not headrush"):
        R.load(schema=path)


def test_the_generator_refuses_a_schema_version_it_does_not_know(schema):
    with pytest.raises(SystemExit, match="schema_version"):
        gen.build(dict(schema, schema_version=99))


def test_the_committed_pair_does_not_drift():
    R.load.cache_clear()
    assert R.load().schema_fingerprint == gen.fingerprint(
        json.loads(R.SCHEMA.read_text()))


# --- AC6: planner-facing lookup across representative blocks ------------

@pytest.mark.parametrize("block, parameter, unit", [
    ("Amp", "Bass", "%"),                 # amp
    ("Cab", "OutGain", "dB"),             # cab
    ("K_Drive", "Drive", "%"),            # drive
    ("AIR_Delay", "Delay", "ms"),         # delay
    ("Volume", "MinVolume", "%"),         # utility
])
def test_representative_parameters_resolve(reg, block, parameter, unit):
    found = reg.resolve(block, parameter)
    assert found.kind == "continuous"
    assert found.unit == unit
    assert found.display_minimum is not None
    assert found.display_maximum is not None


def test_a_block_resolves_by_path_as_well_as_by_name(reg):
    assert reg.block("/Evil/Engine/Patch/Amp") is reg.block("Amp")


def test_an_ambiguous_name_raises_with_the_candidates(reg):
    """Leaf names are not unique across the tree. Picking whichever sorted
    first would hand a caller the AudioCtrl input when it asked for the
    patch's."""
    with pytest.raises(R.UnknownBlock, match="names 2 objects"):
        reg.block("Input")
    assert reg.block("/Evil/Engine/Patch/Input").path.startswith("/Evil/Engine/Patch")


def test_unknown_blocks_and_parameters_raise_distinctly(reg):
    with pytest.raises(R.UnknownBlock):
        reg.block("Not A Block")
    with pytest.raises(R.UnknownParameter):
        reg.resolve("Amp", "NoSuchKnob")


# --- the ModuleType roster, which is what puts a block in a slot --------

def test_an_ordinal_round_trips_to_the_name_the_device_uses(reg):
    assert reg.module_ordinal("Amp") == 1
    assert reg.module_name(1) == "Amp"
    assert reg.module_name(0) == "Empty Slot"


def test_the_patch_furniture_is_present_but_not_selectable(reg):
    """Chain, Input, Output and Mix are real objects that no slot can hold."""
    for name in ("Chain", "Mix", "Output"):
        block = reg.block(f"/Evil/Engine/Patch/{name}")
        assert not block.selectable
        with pytest.raises(R.UnknownBlock, match="no chain slot selects it"):
            reg.module_ordinal(block.path)


def test_the_one_per_rig_modules_are_recorded_not_dropped(reg):
    """ReValver Amp 2, Neural Amp Modeler 2 and C-Verb 2 have ordinals and no
    object. @bschmalz81401 states the rule from the unit as one Capture and
    one C-Verb per rig, and this is that rule in the device's own data. Making
    the join come out even by dropping them would erase it."""
    unbacked = {u["name"]: u["ordinal"] for u in reg.unbacked}
    assert unbacked == {"Empty Slot": 0, "ReValver Amp 2": 4,
                        "Neural Amp Modeler 2": 20, "C-Verb 2": 254}
    for name, ordinal in unbacked.items():
        assert reg.module_name(ordinal) == name
        with pytest.raises(R.UnknownBlock):
            reg.block(name)


def test_every_selectable_block_has_a_distinct_ordinal(reg):
    ordinals = [b.module_ordinal for b in reg.selectable_blocks()]
    assert len(ordinals) == len(set(ordinals)) == 274
    assert ordinals == sorted(ordinals)


# --- what the registry refuses to do ------------------------------------

def test_converting_normalised_to_display_refuses(reg):
    """The device publishes a range, a unit and a 0..1 wire, so the conversion
    looks like arithmetic. It is not: the taper is per parameter."""
    with pytest.raises(R.NotMeasured, match="PER PARAMETER"):
        reg.resolve("Amp", "Bass").to_display(0.75)


def test_the_two_measured_tapers_disagree_with_each_other(reg):
    """The reason the refusal is necessary rather than careful. Amp.Bass is
    linear and Amp.TremSpeed is quadratic, on the same block, with nothing in
    the schema telling them apart. Both are read off the unit's own screen.

    The arithmetic is checked here so the claim in the artifact is not just
    prose: solving lo + x**p * (hi - lo) = shown for p must give 2 for the
    TremSpeed pair and 1 for the Bass one.
    """
    import math

    tapers = reg.wire_encoding["measured_tapers"]
    assert tapers["Amp.Bass"] == "linear"
    assert tapers["Amp.TremSpeed"] == "quadratic"

    def exponent(lo, hi, wire, shown):
        return math.log((shown - lo) / (hi - lo)) / math.log(wire)

    assert exponent(0.25, 20.0, 0.5, 5.19) == pytest.approx(2, abs=0.01)
    assert exponent(0.25, 20.0, 0.25, 1.48) == pytest.approx(2, abs=0.01)
    assert exponent(0.0, 100.0, 0.75, 75.0) == pytest.approx(1, abs=0.01)


def test_the_refusal_names_the_parameter_that_was_asked_about(reg):
    with pytest.raises(R.NotMeasured, match=r"Amp\.TremSpeed:"):
        reg.resolve("Amp", "TremSpeed").to_display(0.5)


def test_owner_state_is_not_in_the_registry(reg):
    """Rig libraries, setlists and the loaded preset name are the owner's.
    The schema already withholds their values; this withholds the objects, so
    a planner cannot offer to rewrite someone's library."""
    for path in ("/Evil/API/Rigs", "/Evil/API/Setlists",
                 "/Evil/Engine/Patch/Rig", "/Evil/Cloud/Master"):
        assert path not in reg.blocks
    blob = json.loads(R.REGISTRY.read_text())
    assert set(blob["excluded_objects"]) >= {"/Evil/API/Rigs", "/Evil/API/Setlists"}


def test_a_parameter_cannot_be_edited(reg):
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        reg.resolve("Amp", "Bass").kind = "selector"


# --- the parameter-set dedup --------------------------------------------

def test_objects_sharing_a_parameter_set_share_one_record():
    """302 objects carry 153 distinct sets, the same reduction the schema does
    on metas. Without it the derived file was larger than its own input."""
    blob = json.loads(R.REGISTRY.read_text())
    assert len(blob["blocks"]) == 302
    assert len(blob["paramsets"]) == 153
    twins = blob["blocks"]["/Evil/Engine/Patch/Amp"]["parameters"]
    assert blob["blocks"]["/Evil/Engine/Patch/Amp_2"]["parameters"] == twins


def test_the_dedup_is_lossless_through_the_loader(reg, schema):
    """Every object still answers with its OWN full parameter set. A shared
    record must not turn into a shared or truncated answer."""
    paths, metas = schema["paths"], schema["metas"]
    for path, block in reg.blocks.items():
        published = metas[paths[path]].get("properties") or {}
        assert set(block.parameters) == set(published), path


def test_a_dangling_parameter_set_reference_is_named(tmp_path):
    """The failure mode the indirection adds. It must not surface as a
    KeyError from inside the loader."""
    blob = json.loads(R.REGISTRY.read_text())
    blob["blocks"]["/Evil/Engine/Patch/Amp"]["parameters"] = "deadbeefdeadbeef"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(blob))
    R.load.cache_clear()
    with pytest.raises(R.SchemaDrift, match="deadbeefdeadbeef"):
        R.load(registry=path, check_drift=False)
    R.load.cache_clear()
