#!/usr/bin/env bash
# Regenerates everything downstream of the C++ scene model:
#
#   cpp/include/toyscene/Scene.h           (the single definition)
#        |  cpp/tools/export_schema.cpp
#        v
#   schema/scene.schema.json               reflect-cpp's JSON Schema, verbatim
#   schema/model_facts.json                what that schema cannot express
#   schema/named_unions.json               the tagged unions it inlines
#        |  scripts/postprocess_schema.py
#        v
#   schema/scene.pydantic.schema.json      input for the code generator
#        |  datamodel-code-generator + scripts/postprocess_models.py
#        v
#   python/toyscene/_models.py             the Pydantic models
#
# All five outputs are committed; tests/test_schema_drift.py fails if running
# this script would change any of them.
#
# Run inside the nix devShell: ./scripts/generate_models.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

build_dir="${TOYSCENE_BUILD_DIR:-build-cpp}"
# Both outputs are redirectable so that tests/test_schema_drift.py can run the
# whole pipeline into a temporary directory and compare, without touching the
# committed files.
schema_dir="${TOYSCENE_SCHEMA_DIR:-schema}"
out="${1:-python/toyscene/_models.py}"
mkdir -p "$schema_dir" "$(dirname "$out")"

echo "==> building the schema exporter in $build_dir"
cmake -S . -B "$build_dir" -DCMAKE_BUILD_TYPE=RelWithDebInfo >/dev/null
cmake --build "$build_dir" --target toyscene_export_schema >/dev/null

echo "==> exporting the schema"
"$build_dir/toyscene_export_schema" "$schema_dir"

echo "==> post-processing the schema"
python scripts/postprocess_schema.py \
  "$schema_dir/scene.schema.json" "$schema_dir/model_facts.json" \
  "$schema_dir/named_unions.json" "$schema_dir/scene.pydantic.schema.json"

echo "==> generating the Pydantic models"
# --strict-nullable         a field with a default is not thereby nullable; C++
#                           rejects null for all of them.
# --use-default-kwarg       `Field(default=...)` rather than a positional.
# --field-constraints       plain Field(ge=..., le=...) instead of con* types.
# --enum-field-as-literal   the tagged-union discriminator becomes
#                           Literal['Mirror'] rather than a one-member Enum.
# --base-class              shared model config, see python/toyscene/_base.py.
# --class-name Scene        names the root model after the schema's root $ref,
#                           so no stray `Model` wrapper is emitted for it.
#
# Note there is deliberately no --collapse-root-models: it would inline the
# hoisted tagged unions back at every use site, undoing the whole point. The
# root models it would have folded away are turned into type aliases by
# scripts/postprocess_models.py instead.
# --custom-file-header      replaces the generator's own header, which carries a
#                           timestamp and would make the output non-reproducible.
datamodel-codegen \
  --input "$schema_dir/scene.pydantic.schema.json" \
  --input-file-type jsonschema \
  --output "$out" \
  --output-model-type pydantic_v2.BaseModel \
  --target-python-version 3.12 \
  --base-class toyscene._base.SceneModel \
  --use-annotated \
  --field-constraints \
  --strict-nullable \
  --use-default-kwarg \
  --enum-field-as-literal all \
  --use-standard-collections \
  --use-union-operator \
  --class-name Scene \
  --custom-file-header "$(cat <<'HEADER'
# GENERATED FILE -- DO NOT EDIT.
#
# Produced from schema/scene.pydantic.schema.json by scripts/generate_models.sh,
# which derives that schema from the C++ definition in
# cpp/include/toyscene/Scene.h. Edit the C++ and regenerate; edits made here are
# lost on the next run and will fail tests/test_schema_drift.py in the meantime.
HEADER
)"

echo "==> swapping in the numpy-backed array types and the union aliases"
python scripts/postprocess_models.py "$out" "$schema_dir/named_unions.json"

echo "==> done"
