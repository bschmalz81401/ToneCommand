"""Issue #5: a family with no usable real-world reference must say so.

The gate in #5 is "assess before building": if no source exists for a family,
report that and stop for that family, never guess. Before this, an unmapped
family (flanger, phaser, wah) was simply absent from the sidecar, which looks
identical to "nobody has checked yet". These tests pin the explicit,
checkable difference: absent-with-a-reason, not silently absent.
"""
from __future__ import annotations

import json
from pathlib import Path

from fm9.registry import Registry

ET_PATH = Path(__file__).resolve().parent.parent / "config" / "effect_type_models.json"


def test_unmapped_families_carry_explicit_no_source_notice():
    data = json.loads(ET_PATH.read_text(encoding="utf-8"))
    no_source = data.get("unmapped_no_source")
    assert no_source, "effect_type_models.json must record which families have no source"
    for fam in ("flanger", "phaser", "wah"):
        assert fam in no_source, f"{fam} is unmapped and must be recorded, not just absent"
        assert len(no_source[fam]) >= 12, f"{fam}'s reason must say what was checked"


def test_a_mapped_family_is_not_also_listed_as_no_source():
    data = json.loads(ET_PATH.read_text(encoding="utf-8"))
    no_source = set(data.get("unmapped_no_source") or {})
    mapped_names = set()
    for section in ("delay_types", "chorus_types", "multitap_types"):
        mapped_names |= set((data.get(section) or {}).keys())
    # families and type names live in different namespaces, but neither
    # "delay" nor "chorus" nor "multitap" (the family names) should ever
    # appear as a no-source entry once they have real mappings.
    assert not ({"delay", "chorus", "multitap"} & no_source)


def test_the_registry_surfaces_the_no_source_notice():
    reg = Registry()
    assert reg.effect_type_models.get("unmapped_no_source")


def test_the_planner_reference_states_the_no_source_families_by_name():
    import server
    ref = server.param_reference()
    assert "no real-world reference has been found for" in ref.lower()
    for fam in ("flanger", "phaser", "wah"):
        assert fam in ref.lower()
