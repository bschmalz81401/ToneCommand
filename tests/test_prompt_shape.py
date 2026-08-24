"""The prompt's reply shape is derived from PLAN_SCHEMA, not hand-copied.

The action-kind list existed in three places and one had drifted: the prompt
advertised six kinds while the schema and the validator accepted eleven. These
tests fail if any of them separate again.
"""
import re

from fm9 import planner

SCHEMA_KINDS = planner.PLAN_SCHEMA["properties"]["actions"]["items"]["properties"]["kind"]["enum"]


def kinds_in(text: str) -> list[str]:
    return re.search(r'"kind": "([^"]+)"', text).group(1).split("|")


def test_the_prompt_advertises_every_kind_the_schema_allows():
    """The drift this file exists to prevent: add_block, bind_pedal, the
    renames and store were all real and all unmentioned."""
    assert kinds_in(planner.plan_shape_line()) == list(SCHEMA_KINDS)


def test_the_validator_accepts_exactly_the_schema_kinds():
    assert list(planner.ACTION_KINDS) == list(SCHEMA_KINDS)


def test_every_advertised_kind_survives_validation():
    plan = {"summary": "", "actions": [{"kind": k} for k in SCHEMA_KINDS]}
    kept = [a["kind"] for a in planner._validate(plan)["actions"]]
    assert kept == list(SCHEMA_KINDS)


def test_an_invented_kind_is_still_dropped():
    plan = {"summary": "", "actions": [{"kind": "set_fire"},
                                       {"kind": "set_param"}]}
    assert [a["kind"] for a in planner._validate(plan)["actions"]] == ["set_param"]


def test_the_shape_line_names_every_action_field():
    """A field added to the schema must appear in the prompt too, or the model
    is never told it exists."""
    shape = planner.plan_shape_line()
    for field in planner.PLAN_SCHEMA["properties"]["actions"]["items"]["properties"]:
        assert f'"{field}":' in shape


def test_nullability_is_carried_across_from_the_schema():
    shape = planner.plan_shape_line()
    assert '"block": str|null' in shape        # ["string", "null"]
    assert '"instance": int' in shape          # plain integer, NOT nullable
    assert '"reason": str' in shape            # plain string, NOT nullable
    assert '"value": number|null' in shape


def test_the_prompt_carries_the_derived_line():
    prompt = planner._full_prompt("more gain", "STATE", "REFERENCE")
    assert planner.plan_shape_line() in prompt


def test_the_shape_line_is_still_valid_json_with_placeholders():
    """It is a template, but its braces and quoting must stay well-formed or
    a model will copy the damage."""
    shape = planner.plan_shape_line()
    assert shape.count("{") == shape.count("}")
    assert shape.count("[") == shape.count("]")
    assert shape.startswith('{"summary": ') and shape.endswith("}")
