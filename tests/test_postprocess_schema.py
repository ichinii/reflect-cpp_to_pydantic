"""Unit tests for scripts/postprocess_schema.py.

The fixtures here are hand-written miniatures rather than slices of the real
schema, so each rule can be pushed into its failure mode without needing a
plausible C++ model behind it.
"""

from __future__ import annotations

import copy

import pytest

from postprocess_schema import (
    SchemaError,
    add_discriminators,
    canonical,
    clean_name,
    flatten_all_of,
    hoist_named_unions,
    is_union_branches,
    postprocess,
    rename_defs,
)


def tagged(name: str, **properties: dict) -> dict:
    """A variant member: an object with a one-value `type` enum."""
    return {
        "type": "object",
        "properties": {"type": {"type": "string", "enum": [name]}, **properties},
        "required": ["type", *properties],
    }


def ref(name: str) -> dict:
    return {"$ref": f"#/$defs/{name}"}


def main_schema() -> dict:
    """One struct whose `kind` field is an inline two-member tagged union."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/$defs/toyscene__Thing",
        "$defs": {
            "toyscene__Thing": {
                "type": "object",
                "properties": {"kind": {"anyOf": [ref("toyscene__A__tagged"), ref("toyscene__B__tagged")]}},
                "required": ["kind"],
            },
            "toyscene__A__tagged": tagged("A"),
            "toyscene__B__tagged": tagged("B"),
        },
    }


def union_document(*members: str, defs: dict | None = None) -> dict:
    """What `rfl::json::to_schema<SomeTaggedUnion>()` produces."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "anyOf": [ref(member) for member in members],
        "$defs": defs if defs is not None else {member: tagged(member.split("__")[1]) for member in members},
    }


# --- helpers ---------------------------------------------------------------


def test_clean_name_strips_the_namespace_and_the_tagged_suffix() -> None:
    assert clean_name("toyscene__Mirror__tagged") == "Mirror"
    assert clean_name("toyscene__Element") == "Element"
    assert clean_name("glm__vec_3__double__glm__packed_highp_") == "Vec3"


def test_is_union_branches_ignores_a_nullable_any_of() -> None:
    assert is_union_branches([ref("A"), ref("B")])
    assert not is_union_branches([ref("A"), {"type": "null"}])
    assert not is_union_branches([ref("A")])
    assert not is_union_branches([{"type": "integer"}, {"type": "null"}])


def test_canonical_is_order_insensitive_for_keys_only() -> None:
    assert canonical({"b": 1, "a": 2}) == canonical({"a": 2, "b": 1})
    assert canonical([1, 2]) != canonical([2, 1])


# --- renaming --------------------------------------------------------------


def test_rename_defs_renames_definitions_and_references() -> None:
    schema = main_schema()
    mapping = rename_defs(schema)

    assert sorted(schema["$defs"]) == ["A", "B", "Thing"]
    assert schema["$ref"] == "#/$defs/Thing"
    assert schema["$defs"]["Thing"]["properties"]["kind"]["anyOf"] == [ref("A"), ref("B")]
    assert mapping["toyscene__A__tagged"] == "A"


def test_rename_defs_keeps_the_tagged_body_when_names_collide() -> None:
    # A struct used both inside a tagged union and on its own gets two
    # definitions; only the tagged one carries the `type` literal.
    schema = main_schema()
    schema["$defs"]["toyscene__A"] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    warnings: list[str] = []
    rename_defs(schema, warn=warnings.append)

    assert "type" in schema["$defs"]["A"]["properties"]
    assert warnings == []  # the plain form was not referenced


def test_rename_defs_warns_when_the_plain_form_is_used_outside_a_union() -> None:
    schema = main_schema()
    schema["$defs"]["toyscene__A"] = {"type": "object", "properties": {}, "required": []}
    schema["$defs"]["toyscene__Thing"]["properties"]["plain"] = ref("toyscene__A")

    warnings: list[str] = []
    rename_defs(schema, warn=warnings.append)

    assert len(warnings) == 1
    assert "toyscene__A" in warnings[0]
    assert "'type'" in warnings[0]
    # The surviving body is still the tagged one, as documented.
    assert "type" in schema["$defs"]["A"]["properties"]


def test_rename_defs_rejects_two_forms_referenced_outside_a_union() -> None:
    schema = main_schema()
    schema["$defs"]["toyscene__A"] = {"type": "object", "properties": {}, "required": []}
    schema["$defs"]["toyscene__Thing"]["properties"]["plain"] = ref("toyscene__A")
    # Now reference the tagged form from outside a union as well.
    schema["$defs"]["toyscene__Thing"]["properties"]["also"] = ref("toyscene__A__tagged")

    with pytest.raises(SchemaError, match="would have to name several different types"):
        rename_defs(schema, warn=lambda _: None)


def test_rename_defs_rejects_a_collision_with_no_tagged_form() -> None:
    schema = main_schema()
    schema["$defs"]["Thing"] = {"type": "object", "properties": {}, "required": []}

    with pytest.raises(SchemaError, match="is the tagged form"):
        rename_defs(schema, warn=lambda _: None)


# --- hoisting --------------------------------------------------------------


def renamed_schema() -> tuple[dict, dict]:
    schema = main_schema()
    mapping = rename_defs(schema)
    return schema, mapping


def test_hoist_named_unions_replaces_the_inline_block() -> None:
    schema, mapping = renamed_schema()
    unions = {"Kind": union_document("toyscene__A__tagged", "toyscene__B__tagged")}

    matches = hoist_named_unions(schema, unions, mapping)

    assert matches == {"Kind": 1}
    assert schema["$defs"]["Thing"]["properties"]["kind"] == ref("Kind")
    assert schema["$defs"]["Kind"] == {"anyOf": [ref("A"), ref("B")]}


def test_hoist_named_unions_replaces_every_occurrence() -> None:
    schema, mapping = renamed_schema()
    inline = {"anyOf": [ref("A"), ref("B")]}
    schema["$defs"]["Thing"]["properties"]["other"] = copy.deepcopy(inline)
    # A second definition, so the union's own carried copy of `A` stays intact.
    schema["$defs"]["Nested"] = {
        "type": "object",
        "properties": {"kind": copy.deepcopy(inline)},
        "required": ["kind"],
    }

    matches = hoist_named_unions(
        schema, {"Kind": union_document("toyscene__A__tagged", "toyscene__B__tagged")}, mapping
    )

    assert matches == {"Kind": 3}
    assert schema["$defs"]["Nested"]["properties"]["kind"] == ref("Kind")
    assert schema["$defs"]["Thing"]["properties"]["other"] == ref("Kind")


def test_hoist_named_unions_leaves_a_nullable_any_of_alone() -> None:
    schema, mapping = renamed_schema()
    nullable = {"anyOf": [ref("A"), {"type": "null"}]}
    schema["$defs"]["Thing"]["properties"]["maybe"] = copy.deepcopy(nullable)

    hoist_named_unions(
        schema, {"Kind": union_document("toyscene__A__tagged", "toyscene__B__tagged")}, mapping
    )

    assert schema["$defs"]["Thing"]["properties"]["maybe"] == nullable


def test_hoist_named_unions_rejects_a_union_that_matches_nothing() -> None:
    schema, mapping = renamed_schema()
    # Members in the other order: a different union, structurally.
    unions = {"Kind": union_document("toyscene__B__tagged", "toyscene__A__tagged")}

    with pytest.raises(SchemaError, match=r"\['Kind'\] do not occur in the schema"):
        hoist_named_unions(schema, unions, mapping)


def test_hoist_named_unions_rejects_an_ambiguous_match() -> None:
    schema, mapping = renamed_schema()
    members = ("toyscene__A__tagged", "toyscene__B__tagged")
    unions = {"Kind": union_document(*members), "Sort": union_document(*members)}

    with pytest.raises(SchemaError, match="matches several registered names"):
        hoist_named_unions(schema, unions, mapping)


def test_hoist_named_unions_rejects_a_name_the_schema_already_uses() -> None:
    schema, mapping = renamed_schema()
    unions = {"Thing": union_document("toyscene__A__tagged", "toyscene__B__tagged")}

    with pytest.raises(SchemaError, match="already defines that name"):
        hoist_named_unions(schema, unions, mapping)


def test_hoist_named_unions_rejects_an_unknown_member() -> None:
    schema, mapping = renamed_schema()
    unions = {"Kind": union_document("toyscene__A__tagged", "toyscene__C__tagged")}

    with pytest.raises(SchemaError, match="member 'toyscene__C__tagged'"):
        hoist_named_unions(schema, unions, mapping)


def test_hoist_named_unions_rejects_a_member_definition_that_disagrees() -> None:
    schema, mapping = renamed_schema()
    document = union_document("toyscene__A__tagged", "toyscene__B__tagged")
    document["$defs"]["toyscene__A__tagged"]["properties"]["extra"] = {"type": "string"}

    with pytest.raises(SchemaError, match="disagree about 'A'"):
        hoist_named_unions(schema, {"Kind": document}, mapping)


def test_hoist_named_unions_rejects_a_non_union_document() -> None:
    schema, mapping = renamed_schema()
    document = {"anyOf": [{"type": "integer"}, {"type": "null"}], "$defs": {}}

    with pytest.raises(SchemaError, match="not an anyOf of references"):
        hoist_named_unions(schema, {"Kind": document}, mapping)


def test_a_hoisted_union_gets_the_discriminator() -> None:
    schema, mapping = renamed_schema()
    hoist_named_unions(
        schema, {"Kind": union_document("toyscene__A__tagged", "toyscene__B__tagged")}, mapping
    )

    assert add_discriminators(schema) == 1
    assert schema["$defs"]["Kind"]["discriminator"] == {"propertyName": "type"}


# --- validator allOf -------------------------------------------------------


def test_flatten_all_of_keeps_the_tightest_bound() -> None:
    # What a composed rfl::Validator arrives as: nested allOf of single-keyword
    # objects. The wide bounds come from the Finite rule.
    node = {
        "allOf": [
            {"minimum": 0.0, "type": "number"},
            {"maximum": 1.0, "type": "number"},
            {"allOf": [{"minimum": -1e308, "type": "number"}, {"maximum": 1e308, "type": "number"}]},
        ]
    }
    assert flatten_all_of(node) == {"type": "number", "minimum": 0.0, "maximum": 1.0}


def test_flatten_all_of_leaves_a_lone_wide_bound_in_place() -> None:
    node = {"allOf": [{"minimum": -1e308, "type": "number"}, {"maximum": 1e308, "type": "number"}]}
    assert flatten_all_of(node) == {"type": "number", "minimum": -1e308, "maximum": 1e308}


def test_flatten_all_of_rejects_branches_that_disagree_on_type() -> None:
    node = {"allOf": [{"minimum": 0, "type": "integer"}, {"maximum": 1, "type": "number"}]}
    with pytest.raises(SchemaError, match="disagree on type"):
        flatten_all_of(node)


# --- the whole thing -------------------------------------------------------


def test_postprocess_runs_the_steps_in_an_order_that_keeps_defaults() -> None:
    # Hoisting has to happen before the defaults are injected, or the `default`
    # sibling stops the inline block from matching the registered union.
    schema = main_schema()
    schema["$defs"]["toyscene__Thing"]["properties"]["kind"] = {
        "anyOf": [ref("toyscene__A__tagged"), ref("toyscene__B__tagged")]
    }
    facts = {
        "bufferRefDefinition": "toyscene__Nothing",
        "defaults": {"Thing": {"kind": {"type": "A"}}},
        "arrays": {},
    }
    unions = {"Kind": union_document("toyscene__A__tagged", "toyscene__B__tagged")}

    result = postprocess(schema, facts, unions)

    assert result["$defs"]["Thing"]["properties"]["kind"] == {
        "$ref": "#/$defs/Kind",
        "default": {"type": "A"},
    }
    assert result["$defs"]["Thing"]["required"] == []
