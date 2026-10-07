#!/usr/bin/env python3
"""The one textual edit applied to the generated models.

`scripts/postprocess_schema.py` gives every buffer-backed array field its own
definition (`Float64Array2D`, `Float64Array1D`), so the generated models already
annotate `SampledSource.positions` as `Float64Array2D`. What the generator
cannot know is that those names should mean "a numpy array passed out of band"
rather than "an object with $buffer/dtype/shape fields".

So: drop the generated placeholder classes and import the real annotated
aliases from `toyscene._arrays` instead. Nothing else about the generated file
is touched -- in particular no field annotation is rewritten.

Run via `scripts/generate_models.sh`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PLACEHOLDER = re.compile(r"^class (Float64Array\d+D)\(", re.MULTILINE)


def drop_class(source: str, start: int) -> tuple[str, int]:
    """Remove the class block beginning at `start` (a line start)."""
    lines = source[start:].splitlines(keepends=True)
    end = len(lines)
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() and not line[0].isspace():
            end = index
            break
    return source[:start] + "".join(lines[end:]), start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="the generated models module")
    args = parser.parse_args()

    source = args.path.read_text()

    names: list[str] = []
    while (match := PLACEHOLDER.search(source)) is not None:
        names.append(match.group(1))
        source, _ = drop_class(source, match.start())

    if not names:
        print(
            "postprocess_models: no Float64Array*D placeholder classes found -- "
            "did postprocess_schema.py stop emitting them?",
            file=sys.stderr,
        )
        return 1

    names = sorted(set(names))
    marker = "from toyscene._base import SceneModel\n"
    if marker not in source:
        print(f"postprocess_models: expected {marker!r} in the generated file", file=sys.stderr)
        return 1

    # The generator runs isort, which groups `toyscene` as first-party or not
    # depending on where the output file happens to live -- so the blank line
    # before this import group is not reproducible. Normalise it here: exactly
    # one blank line, then the two toyscene imports.
    source = re.sub(r"\n+" + re.escape(marker), "\n\n" + marker, source, count=1)
    source = source.replace(
        marker,
        f"from toyscene._arrays import {', '.join(names)}\n{marker}",
        1,
    )

    args.path.write_text(re.sub(r"\n{3,}", "\n\n\n", source).rstrip("\n") + "\n")
    print(f"postprocess_models: imported {', '.join(names)} from toyscene._arrays")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
