"""The tagged unions are named types in Python, not wrappers.

reflect-cpp inlines a `rfl::TaggedUnion` as an `anyOf` at every use site, so
without the hoisting step these names would not exist at all, and without the
alias rewrite they would exist as `RootModel` classes -- which would put a
`.root` between the caller and every variant.
"""

from __future__ import annotations

import typing

import numpy as np
import pytest
from pydantic import BaseModel, RootModel, ValidationError

import toyscene as ts

UNIONS = {
    "Angle": ("Deg", "Rad"),
    "PhotonEnergy": ("Wavelength", "ElectronVolt"),
    "Source": ("PointSource", "RectSource", "SampledSource"),
    "Area": ("RectArea", "EllipseArea"),
    "Behavior": ("Mirror", "Detector", "Grating"),
}


@pytest.mark.parametrize("name", sorted(UNIONS))
def test_each_union_is_re_exported(name: str) -> None:
    assert name in ts.__all__
    assert hasattr(ts, name)


@pytest.mark.parametrize("name,members", sorted(UNIONS.items()))
def test_each_union_is_an_annotated_alias_not_a_model(name: str, members: tuple[str, ...]) -> None:
    alias = getattr(ts, name)

    # Not a class at all, so there is no .root and no second constructor.
    assert not isinstance(alias, type)
    assert typing.get_origin(alias) is typing.Annotated

    union, *metadata = typing.get_args(alias)
    assert {member.__name__ for member in typing.get_args(union)} == set(members)
    assert any(getattr(item, "discriminator", None) == "type" for item in metadata)


def test_no_root_model_survives_in_the_generated_models() -> None:
    from toyscene import _models

    wrappers = [
        name
        for name, value in vars(_models).items()
        if isinstance(value, type) and issubclass(value, RootModel)
    ]
    assert wrappers == []


# --- the annotations ------------------------------------------------------


def test_field_annotations_are_the_alias() -> None:
    # The source really says `divergence: Angle` ...
    assert ts.PointSource.__annotations__["divergence"] == "Angle"
    # ... and it resolves to the alias object itself, metadata included.
    hints = typing.get_type_hints(ts.PointSource, include_extras=True)
    assert hints["divergence"] == ts.Angle
    # Without the metadata, to the union the alias stands for.
    assert typing.get_type_hints(ts.PointSource)["divergence"] == ts.Deg | ts.Rad


@pytest.mark.parametrize(
    "model,field,alias_name",
    [
        ("PointSource", "divergence", "Angle"),
        ("PointSource", "energy", "PhotonEnergy"),
        ("RectSource", "energy", "PhotonEnergy"),
        ("SampledSource", "energy", "PhotonEnergy"),
        ("Element", "area", "Area"),
        ("Element", "behavior", "Behavior"),
        ("Scene", "source", "Source"),
    ],
)
def test_every_union_field_uses_the_alias(model: str, field: str, alias_name: str) -> None:
    hints = typing.get_type_hints(getattr(ts, model), include_extras=True)
    assert hints[field] == getattr(ts, alias_name)


# --- the values -----------------------------------------------------------


def test_a_variant_value_is_the_variant(scene: ts.Scene) -> None:
    assert isinstance(scene.source, ts.SampledSource)
    assert isinstance(scene.elements[0].area, ts.RectArea)
    assert isinstance(scene.elements[0].behavior, ts.Mirror)
    for value in (scene.source, scene.elements[0].area, scene.elements[0].behavior):
        assert not hasattr(value, "root")


def test_the_defaulted_union_field_is_a_variant() -> None:
    source = ts.PointSource(
        type="PointSource", energy=ts.ElectronVolt(type="ElectronVolt", eV=1.0)
    )
    assert isinstance(source.divergence, ts.Rad)
    assert not hasattr(source.divergence, "root")
    assert source.divergence.value == 0.0


def test_a_union_field_discriminates_on_the_tag() -> None:
    source = ts.PointSource.model_validate(
        {
            "type": "PointSource",
            "divergence": {"type": "Rad", "value": 1.0},
            "energy": {"type": "Wavelength", "nm": 1.0},
        }
    )
    assert isinstance(source.divergence, ts.Rad)
    assert source.divergence.value == 1.0
    assert isinstance(source.energy, ts.Wavelength)


def test_an_unknown_tag_is_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        ts.PointSource.model_validate(
            {
                "type": "PointSource",
                "divergence": {"type": "Gradian", "value": 1.0},
                "energy": {"type": "Wavelength", "nm": 1.0},
            }
        )


def test_a_missing_tag_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Unable to extract tag"):
        ts.PointSource.model_validate(
            {"type": "PointSource", "divergence": {"value": 1.0},
             "energy": {"type": "Wavelength", "nm": 1.0}}
        )


def test_an_alias_can_annotate_user_code() -> None:
    # The point of naming them: they are usable in signatures of your own.
    def with_divergence(divergence: ts.Angle) -> ts.PointSource:
        return ts.PointSource(
            type="PointSource",
            divergence=divergence,
            energy=ts.ElectronVolt(type="ElectronVolt", eV=1.0),
        )

    assert typing.get_type_hints(with_divergence, include_extras=True)["divergence"] == ts.Angle
    assert isinstance(with_divergence(ts.Deg(type="Deg", value=3.0)).divergence, ts.Deg)


def test_the_unions_still_round_trip_through_cpp(scene: ts.Scene) -> None:
    # Serialization is unaffected: an alias is not a layer, so the tag is still
    # written where C++ expects it.
    buffers: list[np.ndarray] = []
    document = scene.model_dump_json(context={"buffers": buffers})
    assert '"type":"SampledSource"' in document.replace(", ", ",")
    assert ts._core.simulate_json(document, buffers).shape == (scene.numRays, 7)
