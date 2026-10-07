#!/usr/bin/env python3
"""Turn the reflect-cpp JSON Schema into one datamodel-code-generator can use.

Every step below exists because the exported schema cannot express something,
not because it is inconvenient:

1. **Readable names.** reflect-cpp derives ``$defs`` keys from C++ type names
   with every non-alphanumeric character replaced by ``_``, so ``Mirror``
   arrives as ``toyscene__Mirror__tagged``. Renaming them is what makes the
   generated classes callable from Python.

2. **Flatten validator ``allOf``.** A composed ``rfl::Validator`` becomes a
   nested ``allOf`` of single-keyword objects. Merged into one object, taking
   the tightest bound per keyword -- which is exactly ``allOf`` semantics for
   these keywords, and which is also what makes the ``Finite`` rule's
   ``[-DBL_MAX, DBL_MAX]`` bounds disappear wherever a real bound exists.

3. **Per-field defaults.** reflect-cpp has no per-field notion of "optional
   with a default": ``required`` lists every non-``std::optional`` field, and
   the ``rfl::DefaultIfMissing`` processor flips a single global switch that
   drops ``required`` entirely. The defaults therefore come from
   ``model_facts.json``, which the C++ exporter fills in by reading the actual
   C++ defaults off prototype objects.

4. **Buffer-backed arrays.** ``Array<T>`` serializes as a buffer reference, and
   its schema says nothing about the expected rank -- ``Array<T>`` carries a
   runtime shape. ``model_facts.json`` names the dtype and ndim per field; each
   distinct combination becomes a ``$defs`` entry (``Float64Array2D``, ...)
   whose generated placeholder class ``generate_models.sh`` then swaps for the
   numpy-backed annotated type from ``toyscene._arrays``.

5. **Inline the vec3 definition.** ``glm::dvec3`` arrives as its own ``$defs``
   entry holding ``{"type": "array", "items": {"type": "number"},
   "minItems": 3, "maxItems": 3}``. The length is correctly pinned to 3, so
   nothing needs fixing -- but inlining it at the two use sites turns the
   generated field into a length-constrained list instead of a wrapper model.

6. **Discriminators.** ``rfl::TaggedUnion`` becomes a plain ``anyOf``; adding
   the OpenAPI-style ``discriminator`` makes the generator emit a Pydantic
   discriminated union on ``type``.

Run via ``scripts/generate_models.sh``; it is not meant to be used by hand.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Keywords that may be merged when flattening a validator `allOf`, and the
# function that picks the surviving value when two branches both set one.
TIGHTEST: dict[str, Any] = {
    "minimum": max,
    "exclusiveMinimum": max,
    "maximum": min,
    "exclusiveMaximum": min,
    "minLength": max,
    "maxLength": min,
    "minItems": max,
    "maxItems": min,
}
MERGEABLE = set(TIGHTEST) | {"type"}


class SchemaError(RuntimeError):
    """A mismatch between model_facts.json and the exported schema."""


# --- 1. readable $defs names ------------------------------------------------


def clean_name(raw: str) -> str:
    """`toyscene__Mirror__tagged` -> `Mirror`, `glm__vec_3__...` -> `Vec3`."""
    if raw.startswith("glm__vec_3__"):
        return "Vec3"
    name = re.sub(r"^toyscene__", "", raw)
    name = re.sub(r"__tagged$", "", name)
    return name


def rename_defs(schema: dict[str, Any]) -> dict[str, str]:
    mapping = {raw: clean_name(raw) for raw in schema["$defs"]}
    collisions = {v for v in mapping.values() if list(mapping.values()).count(v) > 1}
    if collisions:
        raise SchemaError(f"$defs names collide after cleaning: {sorted(collisions)}")

    schema["$defs"] = {mapping[raw]: body for raw, body in schema["$defs"].items()}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                node["$ref"] = "#/$defs/" + mapping[ref.removeprefix("#/$defs/")]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return mapping


# --- 2. flatten validator allOf --------------------------------------------


def flatten_all_of(node: Any) -> Any:
    """Merge an `allOf` of plain keyword objects into a single object."""
    if isinstance(node, list):
        return [flatten_all_of(item) for item in node]
    if not isinstance(node, dict):
        return node

    node = {key: flatten_all_of(value) for key, value in node.items()}

    branches = node.get("allOf")
    if not isinstance(branches, list) or len(node) != 1:
        return node
    if not all(isinstance(b, dict) and set(b) <= MERGEABLE for b in branches):
        return node

    merged: dict[str, Any] = {}
    for branch in branches:
        for key, value in branch.items():
            if key not in merged:
                merged[key] = value
            elif key == "type":
                if merged[key] != value:
                    raise SchemaError(f"allOf branches disagree on type: {branches}")
            else:
                merged[key] = TIGHTEST[key](merged[key], value)
    # `type` first reads better in the generated schema.
    return {"type": merged.pop("type"), **merged} if "type" in merged else merged


# --- 3. per-field defaults -------------------------------------------------


def apply_defaults(schema: dict[str, Any], defaults: dict[str, dict[str, Any]]) -> None:
    for def_name, fields in defaults.items():
        body = schema["$defs"].get(def_name)
        if body is None:
            raise SchemaError(
                f"model_facts.json has defaults for {def_name!r}, which is not in "
                f"the schema (available: {sorted(schema['$defs'])})"
            )
        for field, value in fields.items():
            if field not in body.get("properties", {}):
                raise SchemaError(
                    f"model_facts.json has a default for {def_name}.{field!r}, "
                    "which is not a property of that definition"
                )
            if field not in body.get("required", []):
                raise SchemaError(
                    f"{def_name}.{field!r} already is not required; the default "
                    "entry in model_facts.json is stale"
                )
            body["required"] = [f for f in body["required"] if f != field]
            body["properties"][field]["default"] = value


# --- 4. buffer-backed array fields -----------------------------------------


def array_type_name(dtype: str, ndim: int) -> str:
    return f"{dtype.capitalize()}Array{ndim}D"


def apply_arrays(
    schema: dict[str, Any],
    arrays: dict[str, dict[str, dict[str, Any]]],
    buffer_ref_definition: str,
) -> list[str]:
    """Point array fields at one named definition per (dtype, ndim)."""
    buffer_def = clean_name(buffer_ref_definition)
    reference = f"#/$defs/{buffer_def}"
    generated: dict[str, Any] = {}

    for def_name, fields in arrays.items():
        body = schema["$defs"].get(def_name)
        if body is None:
            raise SchemaError(f"model_facts.json names unknown definition {def_name!r}")
        for field, fact in fields.items():
            prop = body.get("properties", {}).get(field)
            if prop is None:
                raise SchemaError(f"{def_name}.{field!r} is not a property")

            name = array_type_name(fact["dtype"], fact["ndim"])
            generated[name] = {
                **buffer_object_schema(schema, buffer_def, fact["dtype"]),
                "x-dtype": fact["dtype"],
                "x-ndim": fact["ndim"],
            }
            new_ref = {"$ref": f"#/$defs/{name}"}

            if prop.get("$ref") == reference:
                body["properties"][field] = new_ref
            elif any(branch == {"$ref": reference} for branch in prop.get("anyOf", [])):
                prop["anyOf"] = [
                    new_ref if branch == {"$ref": reference} else branch
                    for branch in prop["anyOf"]
                ]
            else:
                raise SchemaError(
                    f"{def_name}.{field!r} does not reference the buffer "
                    f"definition {buffer_def!r}; model_facts.json is stale"
                )

    # Drop the original buffer-reference definitions, including the one-hop
    # indirection reflect-cpp emits for the Reflector's nested ReflType.
    for name in list(schema["$defs"]):
        if name == buffer_def or name.startswith("rfl__Reflector_"):
            del schema["$defs"][name]
    schema["$defs"].update(generated)
    return sorted(generated)


def buffer_object_schema(
    schema: dict[str, Any], buffer_def: str, dtype: str
) -> dict[str, Any]:
    """The buffer-reference object, resolved through reflect-cpp's indirection."""
    body = schema["$defs"][buffer_def]
    while "$ref" in body:
        body = schema["$defs"][body["$ref"].removeprefix("#/$defs/")]
    body = json.loads(json.dumps(body))
    body["properties"]["dtype"] = {"const": dtype}
    return body


# --- 5. inline the vec3 definition -----------------------------------------


def inline_def(schema: dict[str, Any], name: str) -> None:
    body = schema["$defs"].pop(name, None)
    if body is None:
        return
    reference = f"#/$defs/{name}"

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if node.get("$ref") == reference:
                return {**json.loads(json.dumps(body)), **{k: v for k, v in node.items() if k != "$ref"}}
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    schema["$defs"] = walk(schema["$defs"])


# --- 6. discriminated unions ----------------------------------------------


def tag_of(schema: dict[str, Any], ref: dict[str, Any]) -> str | None:
    """The single value of the `type` property of a referenced definition."""
    if set(ref) != {"$ref"}:
        return None
    body = schema["$defs"].get(ref["$ref"].removeprefix("#/$defs/"), {})
    values = body.get("properties", {}).get("type", {}).get("enum")
    return values[0] if isinstance(values, list) and len(values) == 1 else None


def add_discriminators(schema: dict[str, Any]) -> int:
    count = 0

    def walk(node: Any) -> None:
        nonlocal count
        if isinstance(node, dict):
            branches = node.get("anyOf")
            if isinstance(branches, list) and len(branches) > 1 and "discriminator" not in node:
                tags = [tag_of(schema, b) for b in branches]
                if all(tags) and len(set(tags)) == len(tags):
                    node["discriminator"] = {"propertyName": "type"}
                    count += 1
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    return count


# --- driver ---------------------------------------------------------------


def postprocess(schema: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    rename_defs(schema)
    schema["$defs"] = flatten_all_of(schema["$defs"])
    apply_defaults(schema, facts["defaults"])
    apply_arrays(schema, facts["arrays"], facts["bufferRefDefinition"])
    inline_def(schema, "Vec3")
    add_discriminators(schema)
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema", type=Path, help="scene.schema.json from export_schema")
    parser.add_argument("facts", type=Path, help="model_facts.json from export_schema")
    parser.add_argument("output", type=Path, help="where to write the result")
    args = parser.parse_args()

    schema = json.loads(args.schema.read_text())
    facts = json.loads(args.facts.read_text())
    try:
        result = postprocess(schema, facts)
    except SchemaError as exc:
        print(f"postprocess_schema: {exc}", file=sys.stderr)
        return 1

    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
