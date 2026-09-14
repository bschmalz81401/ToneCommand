"""The HeadRush amp grounding sidecar, and the rule that it may not guess.

Grounding data lands ahead of any HeadRush adapter on purpose (#33 phase 5):
it needs no device handle and none of the contract work in #109. These tests
therefore read the COMMITTED sidecar and never the source repo, so they pass on
a machine that has never seen a HeadRush or HeadrushRigBuilder.

The behavioural test at the bottom is the one that matters. Everything else
checks shape; that one checks that an ordinal the vendor does not describe
stops the build rather than acquiring a plausible attribution.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SIDECAR = ROOT / "config" / "headrush_amp_models.json"


@pytest.fixture(scope="module")
def data():
    return json.loads(SIDECAR.read_text())


def test_it_declares_what_it_is(data):
    assert data["device"] == "HeadRush"
    assert data["content"] == "facts"
    assert data["generated_by"] == "tools/build_headrush_amp_models.py"
    assert "headrushfx.com" in data["source"], "the vendor's own claim, cited"
    # both inputs are themselves generated, and say when
    assert data["source_generated_at"]
    assert data["device_schema_generated_at"]


def test_hand_edits_are_warned_against(data):
    assert "Generated file" in data["warning"]
    assert "OVERRIDES" in data["warning"], \
        "a reader who wants to fix a name must be told where corrections go"


def test_ordinals_are_contiguous_from_zero(data):
    """A gap means an ordinal silently means nothing, and the device will
    happily send it."""
    for block, rows in data["amps"].items():
        keys = sorted(int(k) for k in rows)
        assert keys == list(range(len(keys))), f"{block} has a gap: {keys}"


def test_every_ordinal_carries_the_device_name(data):
    """`headrush` is the staleness anchor: it must equal the device's own
    Model option at that ordinal. Without it a firmware reorder is silent."""
    for block, rows in data["amps"].items():
        for ordinal, row in rows.items():
            assert row.get("headrush"), f"{block}[{ordinal}] has no device name"
            assert row["headrush"].strip() == row["headrush"]


def test_the_counts_are_not_decorative(data):
    described = sum(1 for rows in data["amps"].values()
                    for r in rows.values() if r.get("model"))
    total = sum(len(rows) for rows in data["amps"].values())
    assert data["counts"] == {"ordinals": total, "described": described,
                              "unattributed": total - described}


def test_facts_only(data):
    """No prose. The vendor publishes an attribution per model and nothing
    else, so a row that grew a third kind of field is a row someone wrote by
    hand."""
    allowed = {"headrush", "model", "unattributed"}
    for block, rows in data["amps"].items():
        for ordinal, row in rows.items():
            extra = set(row) - allowed
            assert not extra, f"{block}[{ordinal}] has unexpected keys {extra}"


def test_an_undescribed_model_is_marked_rather_than_missing(data):
    """The device offers a model the vendor's list does not describe. It is
    recorded as unknown WITH a reason, because a silently absent key and a
    known-absent attribution are different facts."""
    blank = [(b, o, r) for b, rows in data["amps"].items()
             for o, r in rows.items() if r.get("model") is None]
    assert blank, "if the vendor has since described everything, drop this test"
    for block, ordinal, row in blank:
        assert row["unattributed"], f"{block}[{ordinal}] is blank with no reason"


# --- the rule, not the shape -------------------------------------------

def _inputs(device_names, catalog_names):
    """Minimal stand-ins for the two generated artifacts the builder joins."""
    category = "HEADRUSH AMP MODELS (53)"
    schema = {"blocks": {"Amp": {"params": [
        {"name": "Type", "label": "Model", "type": "integer",
         "options": list(device_names)}]}}}
    catalog = {"source": "test", "generatedAt": "now",
               "entries": [{"category": category, "name": n,
                            "inspiredBy": f"a real {n}"} for n in catalog_names]}
    return catalog, schema


def test_the_builder_refuses_to_guess(monkeypatch):
    """An ordinal with no catalog entry and no override must STOP the build.

    This is the whole discipline in one assertion. Fuzzy matching would have
    an answer for every one of these, and measured against the FM9 sidecar it
    maps a Vox AC30 onto an AC15 with 0.80 confidence. A grounding file that
    is confidently wrong is worse than one that is short.
    """
    from tools import build_headrush_amp_models as gen
    monkeypatch.setattr(gen, "BLOCKS", {"Amp": "HEADRUSH AMP MODELS (53)"})
    monkeypatch.setattr(gen, "OVERRIDES", {})
    monkeypatch.setattr(gen, "UNATTRIBUTED", {})
    catalog, schema = _inputs(["59 Tweed Deluxe", "Mystery Amp"],
                              ["59 Tweed Deluxe"])
    with pytest.raises(SystemExit) as err:
        gen.build(catalog, schema)
    assert "Mystery Amp" in str(err.value)
    assert "Do not guess" in str(err.value)


def test_a_spelling_difference_is_fixable_without_touching_the_json(monkeypatch):
    from tools import build_headrush_amp_models as gen
    monkeypatch.setattr(gen, "BLOCKS", {"Amp": "HEADRUSH AMP MODELS (53)"})
    monkeypatch.setattr(gen, "OVERRIDES", {"93 MS-30": "93 MS30"})
    monkeypatch.setattr(gen, "UNATTRIBUTED", {})
    catalog, schema = _inputs(["93 MS-30"], ["93 MS30"])
    built = gen.build(catalog, schema)
    assert built["amps"]["Amp"]["0"]["model"] == "a real 93 MS30"


def test_a_genuine_absence_is_declared_not_overridden(monkeypatch):
    from tools import build_headrush_amp_models as gen
    monkeypatch.setattr(gen, "BLOCKS", {"Amp": "HEADRUSH AMP MODELS (53)"})
    monkeypatch.setattr(gen, "OVERRIDES", {})
    monkeypatch.setattr(gen, "UNATTRIBUTED", {"Ghost Amp": "not published"})
    # a real category with a real entry beside the undescribed one: an empty
    # category means the wrong file was passed, and the builder says so
    catalog, schema = _inputs(["59 Tweed Deluxe", "Ghost Amp"],
                              ["59 Tweed Deluxe"])
    built = gen.build(catalog, schema)
    row = built["amps"]["Amp"]["1"]
    assert row["model"] is None and row["unattributed"] == "not published"
    assert built["counts"] == {"ordinals": 2, "described": 1, "unattributed": 1}


def test_an_empty_category_is_a_wrong_file_not_an_empty_roster(monkeypatch):
    from tools import build_headrush_amp_models as gen
    monkeypatch.setattr(gen, "BLOCKS", {"Amp": "HEADRUSH AMP MODELS (53)"})
    catalog, schema = _inputs(["59 Tweed Deluxe"], [])
    with pytest.raises(SystemExit, match="no category"):
        gen.build(catalog, schema)


def test_the_join_is_case_and_space_only(monkeypatch):
    """Normalisation that went further would start matching different amps."""
    from tools import build_headrush_amp_models as gen
    assert gen.norm("  59   TWEED Deluxe ") == "59 tweed deluxe"
    assert gen.norm("Vox AC30") != gen.norm("Vox AC15")
