#!/usr/bin/env python3
"""Turn the reflect-cpp JSON Schema into one datamodel-code-generator can use.

Every step below exists because the exported schema cannot express something,
not because it is inconvenient:

1. **Readable names.** reflect-cpp derives ``$defs`` keys from C++ type names
   with every non-alphanumeric character replaced by ``_``, so ``Mirror``
   arrives as ``toyscene__Mirror__tagged``. Renaming them is what makes the
   generated classes callable from Python. A struct used *both* inside a
   ``rfl::TaggedUnion`` and on its own gets two definitions, ``Foo`` and
   ``Foo__tagged``, which clean to the same name; see :func:`rename_defs`.

2. **Named unions.** reflect-cpp emits named definitions for structs only. A
   ``rfl::TaggedUnion`` has none: it is inlined as an ``anyOf`` at every use
   site, so ``Angle``, ``PhotonEnergy``, ``Source``, ``Area`` and ``Behavior``
   would not exist as types in the generated Python. ``named_unions.json``
   carries ``rfl::json::to_schema<T>()`` for each union registered in
   ``cpp/src/NamedTypes.h``; every structurally identical inline occurrence is
   replaced by a reference to a definition under that name. A registered union
   that matches nothing, or an inline block that matches two registrations, is
   an error -- the build has to break when the C++ moves and the registry does
   not.

3. **Flatten validator ``allOf``.** A composed ``rfl::Validator`` becomes a
   nested ``allOf`` of single-keyword objects. Merged into one object, taking
   the tightest bound per keyword -- which is exactly ``allOf`` semantics for
   these keywords, and which is also what makes the ``Finite`` rule's
   ``[-DBL_MAX, DBL_MAX]`` bounds disappear wherever a real bound exists.

4. **Per-field defaults.** reflect-cpp has no per-field notion of "optional
   with a default": ``required`` lists every non-``std::optional`` field, and
   the ``rfl::DefaultIfMissing`` processor flips a single global switch that
   drops ``required`` entirely. The defaults therefore come from
   ``model_facts.json``, which the C++ exporter fills in by reading the actual
   C++ defaults off prototype objects.

5. **Buffer-backed arrays.** ``Array<T>`` serializes as a buffer reference, and
   its schema says nothing about the expected rank -- ``Array<T>`` carries a
   runtime shape. ``model_facts.json`` names the dtype and ndim per field; each
   distinct combination becomes a ``$defs`` entry (``Float64Array2D``, ...)
   whose generated placeholder class ``generate_models.sh`` then swaps for the
   numpy-backed annotated type from ``toyscene._arrays``.

6. **Inline the vec3 definition.** ``glm::dvec3`` arrives as its own ``$defs``
   entry holding ``{"type": "array", "items": {"type": "number"},
   "minItems": 3, "maxItems": 3}``. The length is correctly pinned to 3, so
   nothing needs fixing -- but inlining it at the two use sites turns the
   generated field into a length-constrained list instead of a wrapper model.

7. **Discriminators.** A tagged union carries no ``discriminator`` keyword;
   adding the OpenAPI-style one makes the generator emit a Pydantic
   discriminated union on ``type``. After step 2 the only place this applies is
   the hoisted union definitions themselves.

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


DEFS = "$defs"
DEFS_PREFIX = f"#/{DEFS}/"


class SchemaError(RuntimeError):
    """The exported schema and what the C++ exporter says about it disagree."""


def canonical(node: Any) -> Any:
    """A key-sorted copy, for comparing two subschemas structurally."""
    if isinstance(node, dict):
        return {key: canonical(node[key]) for key in sorted(node)}
    if isinstance(node, list):
        return [canonical(item) for item in node]
    return node


def is_union_branches(node: Any) -> bool:
    """True for an `anyOf` list that is nothing but references.

    That is what a `rfl::TaggedUnion` is inlined as. A nullable `std::optional`
    is also an `anyOf`, but one of its branches is `{"type": "null"}`, so it
    does not qualify.
    """
    return (
        isinstance(node, list)
        and len(node) >= 2
        and all(isinstance(branch, dict) and set(branch) == {"$ref"} for branch in node)
    )


def iter_refs(node: Any, in_union: bool = False):
    """Every reference to a definition, and whether it sits inside a union."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(DEFS_PREFIX):
            yield ref.removeprefix(DEFS_PREFIX), in_union
        for key, value in node.items():
            if key == "anyOf" and is_union_branches(value):
                for branch in value:
                    yield from iter_refs(branch, in_union=True)
            else:
                yield from iter_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_refs(item)


def rewrite_refs(node: Any, mapping: dict[str, str]) -> Any:
    """A copy of `node` with every definition reference renamed."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith(DEFS_PREFIX):
                raw = value.removeprefix(DEFS_PREFIX)
                if raw not in mapping:
                    raise SchemaError(f"reference to unknown definition {raw!r}")
                out[key] = DEFS_PREFIX + mapping[raw]
            else:
                out[key] = rewrite_refs(value, mapping)
        return out
    if isinstance(node, list):
        return [rewrite_refs(item, mapping) for item in node]
    return node


# --- 1. readable $defs names ------------------------------------------------


def clean_name(raw: str) -> str:
    """`toyscene__Mirror__tagged` -> `Mirror`, `glm__vec_3__...` -> `Vec3`."""
    if raw.startswith("glm__vec_3__"):
        return "Vec3"
    name = re.sub(r"^toyscene__", "", raw)
    name = re.sub(r"__tagged$", "", name)
    return name


def rename_defs(schema: dict[str, Any], warn=None) -> dict[str, str]:
    """Rename every definition to its clean name and rewrite the references.

    Returns the raw-name -> clean-name mapping, which the union hoisting needs
    in order to rename the references inside the exported union schemas the
    same way.

    A struct that is used both inside a `rfl::TaggedUnion` and on its own gets
    two definitions from reflect-cpp, `toyscene__Foo` and
    `toyscene__Foo__tagged`, which clean to the same name. The tagged form wins:
    it carries the `type` literal that the discriminated union needs, and the
    plain form is otherwise identical to it. References to either then resolve
    to the tagged body. That is wrong for a *plain* field of such a type -- it
    would start demanding a `type` property -- so:

    * if more than one form is referenced from outside a union, there is no
      single body that can serve both and this raises;
    * if only the plain form is, the rename still happens but a warning is
      emitted, because the result is a stricter schema than the C++ accepts.
    """
    warn = warn if warn is not None else lambda message: print(
        f"postprocess_schema: warning: {message}", file=sys.stderr
    )

    groups: dict[str, list[str]] = {}
    for raw in schema[DEFS]:
        groups.setdefault(clean_name(raw), []).append(raw)

    referenced_outside = {raw for raw, in_union in iter_refs(schema) if not in_union}

    mapping: dict[str, str] = {}
    bodies: dict[str, Any] = {}
    for clean, raws in groups.items():
        if len(raws) == 1:
            mapping[raws[0]] = clean
            bodies[clean] = schema[DEFS][raws[0]]
            continue

        tagged = [raw for raw in raws if raw.endswith("__tagged")]
        if len(tagged) != 1:
            raise SchemaError(
                f"{clean!r} is the clean name of {sorted(raws)}, and none of them "
                "is the tagged form, so there is no way to choose between them. "
                "Rename one of the C++ types."
            )

        conflicting = sorted(raw for raw in raws if raw in referenced_outside)
        if len(conflicting) > 1:
            raise SchemaError(
                f"{clean!r} would have to name several different types at once: "
                f"{conflicting} are each referenced from outside a tagged union, "
                "so no single definition can serve them. Rename one of the C++ "
                "types."
            )
        if conflicting:
            warn(
                f"{conflicting[0]!r} is referenced from outside a tagged union but "
                f"shares the clean name {clean!r} with {tagged[0]!r}; the tagged "
                "definition wins, so that field now also requires a 'type' "
                "property, which the C++ side does not"
            )

        for raw in raws:
            mapping[raw] = clean
        bodies[clean] = schema[DEFS][tagged[0]]

    schema[DEFS] = {clean: bodies[clean] for clean in sorted(bodies)}
    renamed = rewrite_refs(schema, mapping)
    schema.clear()
    schema.update(renamed)
    return mapping


# --- 2. named tagged unions ------------------------------------------------


def union_body(name: str, document: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """The `anyOf` root of an exported union schema, with references renamed.

    `document` is `rfl::json::to_schema<T>()` for the union: the `anyOf` sits at
    the document root, with the member definitions carried along under `$defs`.
    Only the root is kept -- the members are already in the main schema, and
    :func:`check_union_members` is what confirms that.
    """
    branches = document.get("anyOf")
    if not is_union_branches(branches):
        raise SchemaError(
            f"the exported schema of union {name!r} is not an anyOf of references, "
            f"so it is not a tagged union: {json.dumps(document.get('anyOf'))}"
        )
    for raw, _ in iter_refs({"anyOf": branches}):
        if raw not in mapping:
            raise SchemaError(
                f"union {name!r} has a member {raw!r} that the main schema does "
                "not define; named_unions.json and scene.schema.json are out of step"
            )
    return {"anyOf": [rewrite_refs(branch, mapping) for branch in branches]}


def check_union_members(
    schema: dict[str, Any], name: str, document: dict[str, Any], mapping: dict[str, str]
) -> None:
    """The definitions a union carries must match the main schema's.

    Both files come out of the same exporter run, so this only ever fires if one
    of them was edited by hand -- which is exactly when a silent mismatch would
    be worst.
    """
    for raw, body in document.get(DEFS, {}).items():
        clean = mapping.get(raw)
        if clean is None or clean not in schema[DEFS]:
            raise SchemaError(
                f"union {name!r} carries a definition {raw!r} that the main schema "
                "does not have"
            )
        if canonical(rewrite_refs(body, mapping)) != canonical(schema[DEFS][clean]):
            raise SchemaError(
                f"union {name!r} and the main schema disagree about {clean!r}; "
                "regenerate both with scripts/generate_models.sh"
            )


def hoist_named_unions(
    schema: dict[str, Any], unions: dict[str, Any], mapping: dict[str, str]
) -> dict[str, int]:
    """Replace inline tagged unions with references to a named definition.

    Returns the number of sites replaced per union name. Raises if a registered
    union matches no site -- the registry in `cpp/src/NamedTypes.h` is then
    stale -- or if one inline block matches two registrations, which would make
    the choice of name arbitrary.
    """
    targets: dict[str, dict[str, Any]] = {}
    for name in sorted(unions):
        if name in schema[DEFS]:
            raise SchemaError(
                f"cannot hoist union {name!r}: the schema already defines that name"
            )
        body = union_body(name, unions[name], mapping)
        check_union_members(schema, name, unions[name], mapping)
        targets[name] = body

    canonical_targets = {name: canonical(body) for name, body in targets.items()}
    matches = dict.fromkeys(targets, 0)

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "anyOf" in node:
                hits = [
                    name
                    for name, target in canonical_targets.items()
                    if canonical(node) == target
                ]
                if len(hits) > 1:
                    raise SchemaError(
                        f"an inline union matches several registered names {sorted(hits)}, "
                        "so which one it should become is arbitrary. Those unions have "
                        "identical members; drop one from cpp/src/NamedTypes.h."
                    )
                if hits:
                    matches[hits[0]] += 1
                    return {"$ref": DEFS_PREFIX + hits[0]}
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    hoisted = walk(schema)
    schema.clear()
    schema.update(hoisted)

    unmatched = sorted(name for name, count in matches.items() if count == 0)
    if unmatched:
        raise SchemaError(
            f"registered union(s) {unmatched} do not occur in the schema. Either the "
            "C++ alias is no longer used, or its members changed; update "
            "cpp/src/NamedTypes.h."
        )

    for name, body in targets.items():
        schema[DEFS][name] = body
    schema[DEFS] = {key: schema[DEFS][key] for key in sorted(schema[DEFS])}
    return matches


# --- 3. flatten validator allOf --------------------------------------------


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


# --- 4. per-field defaults -------------------------------------------------


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


# --- 5. buffer-backed array fields -----------------------------------------


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


# --- 6. inline the vec3 definition -----------------------------------------


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


# --- 7. discriminated unions ----------------------------------------------


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


def postprocess(
    schema: dict[str, Any], facts: dict[str, Any], unions: dict[str, Any]
) -> dict[str, Any]:
    mapping = rename_defs(schema)
    # Before apply_defaults, while the inline union sites are still bare
    # `{"anyOf": [...]}` with no sibling keys to spoil the structural match.
    hoist_named_unions(schema, unions, mapping)
    schema[DEFS] = flatten_all_of(schema[DEFS])
    apply_defaults(schema, facts["defaults"])
    apply_arrays(schema, facts["arrays"], facts["bufferRefDefinition"])
    inline_def(schema, "Vec3")
    add_discriminators(schema)
    return schema


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("schema", type=Path, help="scene.schema.json from export_schema")
    parser.add_argument("facts", type=Path, help="model_facts.json from export_schema")
    parser.add_argument("unions", type=Path, help="named_unions.json from export_schema")
    parser.add_argument("output", type=Path, help="where to write the result")
    args = parser.parse_args()

    schema = json.loads(args.schema.read_text())
    facts = json.loads(args.facts.read_text())
    unions = json.loads(args.unions.read_text())
    try:
        result = postprocess(schema, facts, unions)
    except SchemaError as exc:
        print(f"postprocess_schema: {exc}", file=sys.stderr)
        return 1

    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
