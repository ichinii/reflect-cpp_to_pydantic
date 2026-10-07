#!/usr/bin/env python3
"""The textual edits applied to the generated models.

Three of them, each because datamodel-code-generator cannot be told to do it:

1. **Numpy-backed arrays.** `scripts/postprocess_schema.py` gives every
   buffer-backed array field its own definition (`Float64Array2D`,
   `Float64Array1D`), so the generated models already annotate
   `SampledSource.positions` as `Float64Array2D`. What the generator cannot know
   is that those names should mean "a numpy array passed out of band" rather
   than "an object with $buffer/dtype/shape fields". So the generated
   placeholder classes are dropped and the real annotated aliases are imported
   from `toyscene._arrays` instead. No field annotation is rewritten.

2. **Named unions as aliases.** A hoisted tagged union is a `$defs` entry whose
   body is an `anyOf`, and the generator emits those as `RootModel` subclasses:

       class Angle(RootModel[Deg | Rad]):
           root: Annotated[Deg | Rad, Field(discriminator='type')]

   That would force `.root` on every access and make `isinstance(x, Deg)` false.
   datamodel-code-generator 0.35.0 has no option to emit a type alias instead
   (`--collapse-root-models` does the opposite: it inlines the union back at
   every use site, undoing the hoist), so each such class is rewritten into the
   alias it should have been:

       Angle = Annotated[Deg | Rad, Field(discriminator='type')]

   The union names are passed in, and a registered union that did not come out
   as a `RootModel` is an error -- otherwise a generator change could silently
   leave a wrapper class behind.

3. **Empty `Field()` wrappers.** With a default and `--use-annotated`, the
   generator writes `divergence: Annotated[Angle, Field()] = ...`. The empty
   `Field()` adds nothing, and it stops the annotation from *being* the alias,
   so `Annotated[<alias>, Field()]` collapses back to `<alias>`.

Run via `scripts/generate_models.sh`.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ARRAY_PLACEHOLDER = re.compile(r"^class (Float64Array\d+D)\(", re.MULTILINE)
BASE_IMPORT = "from toyscene._base import SceneModel\n"


class ModelsError(RuntimeError):
    """The generated file does not look the way the next step needs it to."""


def splice(source: str, edits: list[tuple[int, int, str]]) -> str:
    """Replace line ranges (1-based, inclusive) with text; "" deletes them."""
    lines = source.splitlines(keepends=True)
    for start, end, text in sorted(edits, key=lambda edit: edit[0], reverse=True):
        lines[start - 1 : end] = [text] if text else []
    return "".join(lines)


def render(node: ast.AST | None) -> str:
    """A node back as source, on one line and canonically spaced.

    `ast.unparse` rather than the original source segment: the generator wraps
    long annotations across lines, and re-joining those by hand would have to
    guess where the spaces belong.
    """
    return ast.unparse(node) if node is not None else ""


def collapse_blank_lines_between(source: str, names: list[str]) -> str:
    """One blank line between adjacent alias assignments, not two."""
    if not names:
        return source
    line = r"^(?:%s) = .*\n" % "|".join(re.escape(name) for name in names)
    return re.sub(f"({line})\n+(?={line})", r"\1\n", source, flags=re.MULTILINE)


# --- 1. numpy-backed arrays ------------------------------------------------


def drop_array_placeholders(source: str) -> tuple[str, list[str]]:
    """Remove the generated buffer-reference classes, returning their names."""
    names: list[str] = []
    while (match := ARRAY_PLACEHOLDER.search(source)) is not None:
        names.append(match.group(1))
        lines = source[match.start() :].splitlines(keepends=True)
        end = len(lines)
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() and not line[0].isspace():
                end = index
                break
        source = source[: match.start()] + "".join(lines[end:])
    return source, sorted(set(names))


def import_arrays(source: str, names: list[str]) -> str:
    if BASE_IMPORT not in source:
        raise ModelsError(f"expected {BASE_IMPORT!r} in the generated file")
    # The generator runs isort, which groups `toyscene` as first-party or not
    # depending on where the output file happens to live -- so the blank line
    # before this import group is not reproducible. Normalise it here: exactly
    # one blank line, then the toyscene imports.
    source = re.sub(r"\n+" + re.escape(BASE_IMPORT), "\n\n" + BASE_IMPORT, source, count=1)
    return source.replace(
        BASE_IMPORT, f"from toyscene._arrays import {', '.join(names)}\n{BASE_IMPORT}", 1
    )


# --- 2. named unions as aliases -------------------------------------------


def find_root_models(source: str) -> list[tuple[int, int, str, str]]:
    """Locate `class X(RootModel[...]): root: <annotation>` declarations.

    Returns (first line, last line, class name, annotation source) per match.
    """
    found = []
    for node in ast.parse(source).body:
        if not isinstance(node, ast.ClassDef) or len(node.bases) != 1:
            continue
        base = node.bases[0]
        if not (
            isinstance(base, ast.Subscript)
            and isinstance(base.value, ast.Name)
            and base.value.id == "RootModel"
        ):
            continue
        body = [
            statement
            for statement in node.body
            if not (
                isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant)
            )
        ]
        if len(body) != 1:
            continue
        statement = body[0]
        if not (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "root"
            and statement.value is None
        ):
            continue
        found.append((node.lineno, node.end_lineno, node.name, render(statement.annotation)))
    return found


def rewrite_root_models(source: str, expected: list[str]) -> tuple[str, list[str]]:
    """Turn root-model wrappers into plain type aliases."""
    found = find_root_models(source)
    names = [name for _, _, name, _ in found]

    missing = sorted(set(expected) - set(names))
    if missing:
        raise ModelsError(
            f"the generator did not emit a root model for {missing}, so there is "
            "nothing to turn into a type alias. Either those unions were not "
            "hoisted into the schema, or the generator no longer wraps an anyOf "
            f"definition in a RootModel (it emitted: {sorted(names)})"
        )

    source = splice(
        source,
        [(start, end, f"{name} = {annotation}\n") for start, end, name, annotation in found],
    )
    source = collapse_blank_lines_between(source, names)
    # `RootModel` is now unused.
    source = re.sub(r"^from pydantic import (.*)$", _drop_root_model, source, count=1, flags=re.MULTILINE)
    return source, names


def _drop_root_model(match: re.Match[str]) -> str:
    kept = [name for name in match.group(1).split(", ") if name != "RootModel"]
    return f"from pydantic import {', '.join(kept)}" if kept else ""


# --- 3. empty Field() wrappers --------------------------------------------


def simplify_alias_annotations(source: str) -> str:
    """`x: Annotated[Alias, Field()] = d` -> `x: Alias = d`.

    Only for a bare name inside the `Annotated`, so this can only ever strip a
    wrapper from an alias reference, never from a real constraint.
    """
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        annotation = node.annotation
        if not (
            isinstance(annotation, ast.Subscript)
            and isinstance(annotation.value, ast.Name)
            and annotation.value.id == "Annotated"
            and isinstance(annotation.slice, ast.Tuple)
            and len(annotation.slice.elts) == 2
        ):
            continue
        inner, metadata = annotation.slice.elts
        if not isinstance(inner, ast.Name):
            continue
        if not (
            isinstance(metadata, ast.Call)
            and isinstance(metadata.func, ast.Name)
            and metadata.func.id == "Field"
            and not metadata.args
            and not metadata.keywords
        ):
            continue
        text = f"{' ' * node.col_offset}{node.target.id}: {inner.id}"
        if node.value is not None:
            text += f" = {render(node.value)}"
        edits.append((node.lineno, node.end_lineno, text + "\n"))
    return splice(source, edits)


# --- driver ---------------------------------------------------------------


def postprocess(source: str, unions: list[str]) -> tuple[str, list[str], list[str]]:
    source, arrays = drop_array_placeholders(source)
    if not arrays:
        raise ModelsError(
            "no Float64Array*D placeholder classes found -- did "
            "postprocess_schema.py stop emitting them?"
        )
    source = import_arrays(source, arrays)
    source, aliases = rewrite_root_models(source, unions)
    source = simplify_alias_annotations(source)
    return re.sub(r"\n{3,}", "\n\n\n", source).rstrip("\n") + "\n", arrays, aliases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="the generated models module")
    parser.add_argument(
        "unions", type=Path, help="named_unions.json, naming the unions to expect"
    )
    args = parser.parse_args()

    unions = sorted(json.loads(args.unions.read_text()))
    try:
        source, arrays, aliases = postprocess(args.path.read_text(), unions)
    except ModelsError as exc:
        print(f"postprocess_models: {exc}", file=sys.stderr)
        return 1

    args.path.write_text(source)
    print(f"postprocess_models: imported {', '.join(arrays)} from toyscene._arrays")
    print(f"postprocess_models: aliased {', '.join(sorted(aliases))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
