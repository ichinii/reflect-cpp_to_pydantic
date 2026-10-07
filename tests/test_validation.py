"""What each side rejects.

Value ranges live in the C++ type aliases, reach the JSON Schema, and from there
the Pydantic models -- so the same violation has to be caught twice. Each case
below is checked against the models *and* against C++ with the model layer
bypassed.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

import toyscene as ts
from conftest import OVERFLOW, raw_scene, simulate_raw

NAN = float("nan")


def set_in(document: dict, path: str, value: object) -> dict:
    """`set_in(doc, "source.energy.eV", -1.0)`, with [i] for list indices."""
    node = document
    keys = path.replace("[", ".").replace("]", "").split(".")
    for key in keys[:-1]:
        node = node[int(key)] if key.isdigit() else node[key]
    last = keys[-1]
    node[int(last) if last.isdigit() else last] = value
    return document


# (description, path in the raw document, offending value, text C++ should say)
RANGE_VIOLATIONS = [
    ("reflectivity above 1", "elements[0].behavior.reflectivity", 1.5, "less than or equal to 1"),
    ("reflectivity below 0", "elements[0].behavior.reflectivity", -0.1, "greater than or equal to 0"),
    ("numRays of zero", "numRays", 0, "greater than or equal to 1"),
    ("numRays beyond the cap", "numRays", 10_000_001, "less than or equal to 10000000"),
    ("empty name", "name", "", "Size validation failed"),
    ("non-positive energy", "source.energy.eV", -1.0, "greater than 0"),
    ("zero width", "elements[0].area.width", 0.0, "greater than 0"),
]


@pytest.mark.parametrize(
    "path,value,message",
    [pytest.param(p, v, m, id=name) for name, p, v, m in RANGE_VIOLATIONS],
)
def test_cpp_rejects_range_violations(path: str, value: object, message: str) -> None:
    with pytest.raises(ts.SceneError, match=r".*"):
        simulate_raw(set_in(raw_scene(), path, value))

    try:
        simulate_raw(set_in(raw_scene(), path, value))
    except ts.SceneError as error:
        assert message in str(error)


def test_pydantic_rejects_reflectivity_above_one() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        ts.Mirror(type="Mirror", reflectivity=1.5)


def test_pydantic_rejects_reflectivity_below_zero() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ts.Mirror(type="Mirror", reflectivity=-0.1)


def test_pydantic_rejects_ray_counts_outside_the_range(scene: ts.Scene) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        ts.Scene(name="s", numRays=0, source=scene.source, elements=[])
    with pytest.raises(ValidationError, match="less than or equal to 10000000"):
        ts.Scene(name="s", numRays=10_000_001, source=scene.source, elements=[])


def test_pydantic_rejects_an_empty_name(scene: ts.Scene) -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        ts.Scene(name="", source=scene.source, elements=[])


def test_pydantic_rejects_non_positive_energy() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        ts.ElectronVolt(type="ElectronVolt", eV=0.0)
    with pytest.raises(ValidationError, match="greater than 0"):
        ts.Wavelength(type="Wavelength", nm=-1.0)


def test_pydantic_rejects_a_zero_sized_area() -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        ts.RectArea(type="RectArea", width=0.0, height=1.0)
    with pytest.raises(ValidationError, match="greater than 0"):
        ts.EllipseArea(type="EllipseArea", a=1.0, b=0.0)


def test_pydantic_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ts.Mirror(type="Mirror", reflectivity=0.5, reflektivity=0.5)


def test_cpp_rejects_unknown_fields() -> None:
    with pytest.raises(ts.SceneError):
        simulate_raw(raw_scene(unexpected=1))


# --- NaN ------------------------------------------------------------------
#
# The guards reject NaN on both sides, but for different reasons: Pydantic reads
# a bound as "require in range", which NaN fails, while reflect-cpp reads it as
# "fail if out of range", which NaN passes -- hence the custom Finite rule. The
# rule reaches the schema as the finite double range, which is also what makes
# the Pydantic side reject NaN for fields whose only guard is finiteness.


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda: ts.Mirror(type="Mirror", reflectivity=NAN), id="reflectivity"),
        pytest.param(lambda: ts.ElectronVolt(type="ElectronVolt", eV=NAN), id="eV"),
        pytest.param(lambda: ts.RectArea(type="RectArea", width=NAN, height=1.0), id="width"),
        pytest.param(lambda: ts.Deg(type="Deg", value=NAN), id="angle-only-guarded-by-finite"),
        pytest.param(lambda: ts.Rad(type="Rad", value=math.inf), id="angle-infinity"),
    ],
)
def test_pydantic_rejects_non_finite_guarded_floats(factory) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_a_non_finite_scalar_never_reaches_a_cpp_guard() -> None:
    # Standard JSON has no way to spell NaN or infinity, and reflect-cpp's reader
    # enforces that: the non-standard `NaN` literal is a syntax error, and an
    # exponent that overflows a double is rejected while the number is read. So a
    # guarded scalar is unreachable by a non-finite value *through JSON* -- the
    # Finite rule earns its keep on scenes built in C++, on assignment, on bulk
    # buffers, and in the exported schema (where it is what makes the Pydantic
    # models reject NaN).
    with pytest.raises(ts.SceneError, match="number is infinity"):
        simulate_raw(set_in(raw_scene(), "elements[0].area.width", OVERFLOW))

    with pytest.raises(ts.SceneError, match="Could not parse document"):
        simulate_raw(set_in(raw_scene(), "elements[0].area.width", NAN))


# --- dvec3 ----------------------------------------------------------------


@pytest.mark.parametrize("value", [[0.0, 1.0], [0.0, 0.0, 1.0, 0.0], []])
def test_pydantic_rejects_vec3_of_the_wrong_length(value: list[float]) -> None:
    with pytest.raises(ValidationError, match="(at least|at most) 3 items"):
        ts.Element(
            name="e",
            normal=value,
            area=ts.RectArea(type="RectArea", width=1.0, height=1.0),
            behavior=ts.Detector(type="Detector"),
        )


@pytest.mark.parametrize("value", [[0.0, 1.0], [0.0, 0.0, 1.0, 0.0]])
def test_cpp_rejects_vec3_of_the_wrong_length(value: list[float]) -> None:
    with pytest.raises(ts.SceneError, match="normal"):
        simulate_raw(set_in(raw_scene(), "elements[0].normal", value))


def test_a_non_unit_normal_is_a_cpp_only_rule() -> None:
    # Nothing in a JSON Schema can say "length 1", so Pydantic accepts this ...
    element = ts.Element(
        name="e",
        normal=[0.0, 0.0, 0.5],
        area=ts.RectArea(type="RectArea", width=1.0, height=1.0),
        behavior=ts.Detector(type="Detector"),
    )
    assert element.normal == [0.0, 0.0, 0.5]

    # ... and C++ is where it is caught, naming the field.
    with pytest.raises(ts.SceneError, match=r"elements\[0\].normal: expected a unit vector"):
        simulate_raw(set_in(raw_scene(), "elements[0].normal", [0.0, 0.0, 0.5]))


def test_a_non_finite_position_is_rejected() -> None:
    # Same as above: this fails while the number is read, so Element::validate's
    # finiteness check never gets a chance here. It is still worth having, for
    # scenes assembled in C++ -- see cpp/tests/test_scene.cpp.
    with pytest.raises(ts.SceneError, match="number is infinity"):
        simulate_raw(set_in(raw_scene(), "elements[0].position", [OVERFLOW, 0.0, 0.0]))


def test_cpp_rejects_a_missing_field() -> None:
    # A C++ default initializer does not make the field optional in the JSON:
    # Pydantic fills defaults in before the document is written.
    document = raw_scene()
    del document["numRays"]
    with pytest.raises(ts.SceneError, match="numRays"):
        simulate_raw(document)


def test_cpp_rejects_an_unknown_variant_tag() -> None:
    with pytest.raises(ts.SceneError):
        simulate_raw(set_in(raw_scene(), "elements[0].behavior.type", "Lens"))


def test_pydantic_rejects_an_unknown_variant_tag() -> None:
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        ts.Element(
            name="e",
            area=ts.RectArea(type="RectArea", width=1.0, height=1.0),
            behavior={"type": "Lens"},
        )
