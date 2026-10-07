"""The committed generated files must match what the pipeline produces.

`schema/scene.schema.json`, `schema/model_facts.json`,
`schema/named_unions.json`, `schema/scene.pydantic.schema.json` and
`python/toyscene/_models.py` are all derived from the C++ headers and all
committed. This runs the full pipeline into
a temporary directory and compares, so a change to the C++ model that has not
been propagated fails here rather than at some later surprise.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO_ROOT

GENERATED = [
    "schema/scene.schema.json",
    "schema/model_facts.json",
    "schema/named_unions.json",
    "schema/scene.pydantic.schema.json",
]


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    for tool in ("cmake", "datamodel-codegen"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} is not on PATH; run the tests inside the nix devShell")

    out = tmp_path_factory.mktemp("regenerated")
    environment = {
        **os.environ,
        "TOYSCENE_SCHEMA_DIR": str(out / "schema"),
        "TOYSCENE_BUILD_DIR": str(REPO_ROOT / "build-cpp"),
    }
    result = subprocess.run(
        ["./scripts/generate_models.sh", str(out / "_models.py")],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"generate_models.sh failed:\n{result.stdout}\n{result.stderr}")
    return out


@pytest.mark.parametrize("relative", GENERATED)
def test_schema_files_are_up_to_date(regenerated: Path, relative: str) -> None:
    committed = (REPO_ROOT / relative).read_text()
    fresh = (regenerated / relative).read_text()
    assert fresh == committed, (
        f"{relative} is stale -- run ./scripts/generate_models.sh and commit the result"
    )


def test_models_are_up_to_date(regenerated: Path) -> None:
    committed = (REPO_ROOT / "python/toyscene/_models.py").read_text()
    fresh = (regenerated / "_models.py").read_text()
    assert fresh == committed, (
        "python/toyscene/_models.py is stale -- run ./scripts/generate_models.sh "
        "and commit the result"
    )


def test_the_exported_schema_matches_the_running_extension() -> None:
    # The committed schema and the compiled extension must come from the same
    # headers; an unrebuilt extension would make every other test lie.
    import toyscene as ts

    assert ts.scene_json_schema().rstrip("\n") == (
        REPO_ROOT / "schema/scene.schema.json"
    ).read_text().rstrip("\n")
