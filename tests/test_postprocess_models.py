"""Unit tests for scripts/postprocess_models.py."""

from __future__ import annotations

import textwrap

import pytest

from postprocess_models import (
    ModelsError,
    drop_array_placeholders,
    rewrite_root_models,
    simplify_alias_annotations,
)

GENERATED = '''\
from typing import Annotated, Literal

from pydantic import Field, RootModel

from toyscene._base import SceneModel


class Deg(SceneModel):
    type: Literal['Deg']


class Rad(SceneModel):
    type: Literal['Rad']


class Angle(RootModel[Deg | Rad]):
    root: Annotated[Deg | Rad, Field(discriminator='type')]


class PointSource(SceneModel):
    divergence: Annotated[Angle, Field()] = {'type': 'Rad', 'value': 0.0}
'''


def test_rewrite_root_models_produces_a_type_alias() -> None:
    source, names = rewrite_root_models(GENERATED, ["Angle"])

    assert names == ["Angle"]
    assert "Angle = Annotated[Deg | Rad, Field(discriminator='type')]\n" in source
    assert "RootModel" not in source


def test_rewrite_root_models_keeps_the_alias_where_the_class_was() -> None:
    # Order matters: a module-level alias has to be defined before the class
    # that annotates a field with it.
    source, _ = rewrite_root_models(GENERATED, ["Angle"])
    assert source.index("Angle = ") < source.index("class PointSource")
    assert source.index("class Rad") < source.index("Angle = ")


def test_rewrite_root_models_joins_a_wrapped_annotation() -> None:
    wrapped = GENERATED.replace(
        "    root: Annotated[Deg | Rad, Field(discriminator='type')]",
        "    root: Annotated[\n        Deg | Rad, Field(discriminator='type')\n    ]",
    )
    source, _ = rewrite_root_models(wrapped, ["Angle"])
    assert "Angle = Annotated[Deg | Rad, Field(discriminator='type')]\n" in source


def test_rewrite_root_models_collapses_blank_lines_between_aliases() -> None:
    two = GENERATED.replace(
        "class Angle(RootModel[Deg | Rad]):\n    root: Annotated[Deg | Rad, Field(discriminator='type')]",
        "class Angle(RootModel[Deg | Rad]):\n"
        "    root: Annotated[Deg | Rad, Field(discriminator='type')]\n"
        "\n"
        "\n"
        "class Other(RootModel[Deg | Rad]):\n"
        "    root: Annotated[Deg | Rad, Field(discriminator='type')]",
    )
    source, names = rewrite_root_models(two, ["Angle", "Other"])
    assert sorted(names) == ["Angle", "Other"]
    assert "Field(discriminator='type')]\n\nOther = " in source


def test_rewrite_root_models_rejects_a_union_the_generator_did_not_wrap() -> None:
    with pytest.raises(ModelsError, match=r"did not emit a root model for \['PhotonEnergy'\]"):
        rewrite_root_models(GENERATED, ["Angle", "PhotonEnergy"])


def test_rewrite_root_models_ignores_a_root_model_with_a_bigger_body() -> None:
    # Only the plain `root: <annotation>` shape is an alias in disguise.
    odd = GENERATED.replace(
        "    root: Annotated[Deg | Rad, Field(discriminator='type')]",
        "    root: Annotated[Deg | Rad, Field(discriminator='type')]\n"
        "    extra: int = 1",
    )
    with pytest.raises(ModelsError, match="did not emit a root model"):
        rewrite_root_models(odd, ["Angle"])


def test_simplify_alias_annotations_drops_the_empty_field() -> None:
    source = simplify_alias_annotations(GENERATED)
    assert "    divergence: Angle = {'type': 'Rad', 'value': 0.0}\n" in source


def test_simplify_alias_annotations_leaves_real_constraints_alone() -> None:
    source = textwrap.dedent(
        """\
        class Mirror(SceneModel):
            reflectivity: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
            name: Annotated[str, Field(min_length=1)]
        """
    )
    assert simplify_alias_annotations(source) == source


def test_simplify_alias_annotations_leaves_a_wrapped_union_alone() -> None:
    # Not a bare name inside the Annotated, so not an alias reference.
    source = "class X(SceneModel):\n    y: Annotated[Deg | Rad, Field()] = None\n"
    assert simplify_alias_annotations(source) == source


def test_drop_array_placeholders_removes_the_buffer_reference_classes() -> None:
    source = textwrap.dedent(
        """\
        class Float64Array2D(SceneModel):
            field_buffer: Annotated[int, Field(alias='$buffer')]
            dtype: Literal['float64']


        class Element(SceneModel):
            name: str
        """
    )
    stripped, names = drop_array_placeholders(source)

    assert names == ["Float64Array2D"]
    assert "Float64Array2D" not in stripped
    assert "class Element" in stripped
