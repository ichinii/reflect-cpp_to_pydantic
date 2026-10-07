"""The happy path: a scene goes in, rays come out."""

from __future__ import annotations

import json

import numpy as np
import pytest

import toyscene as ts
from conftest import raw_buffers, raw_scene, simulate_raw


def test_result_shape_and_dtype(scene: ts.Scene) -> None:
    rays = ts.simulate(scene)
    assert rays.shape == (scene.numRays, ts.RESULT_COLUMNS)
    assert rays.dtype == np.float64
    assert np.isfinite(rays).all()


def test_energy_column_is_electron_volts(scene: ts.Scene) -> None:
    rays = ts.simulate(scene)
    assert np.allclose(rays[:, 6], 250.0)


def test_wavelength_is_converted_to_electron_volts(scene: ts.Scene) -> None:
    scene.source.energy = ts.Wavelength(type="Wavelength", nm=4.959367937328)
    rays = ts.simulate(scene)
    assert rays[:, 6] == pytest.approx(250.0, abs=1e-6)


def test_a_seed_makes_the_result_reproducible(scene: ts.Scene) -> None:
    assert np.array_equal(ts.simulate(scene), ts.simulate(scene))


def test_a_different_seed_changes_the_result(scene: ts.Scene) -> None:
    first = ts.simulate(scene)
    scene.seed = scene.seed + 1
    assert not np.array_equal(first, ts.simulate(scene))


def test_moving_an_element_changes_the_result(scene: ts.Scene) -> None:
    first = ts.simulate(scene)
    scene.elements[0].position = [10.0, 0.0, 1000.0]
    assert not np.array_equal(first, ts.simulate(scene))


def test_tilting_an_element_changes_the_result(scene: ts.Scene) -> None:
    first = ts.simulate(scene)
    scene.elements[0].normal = [0.0, 2.0**-0.5, 2.0**-0.5]
    assert not np.array_equal(first, ts.simulate(scene))


def test_the_result_does_not_own_its_data(scene: ts.Scene) -> None:
    # The array Python gets is the simulation's own output buffer, handed over
    # through a capsule rather than copied.
    rays = ts.simulate(scene)
    assert not rays.flags.owndata
    assert rays.base is not None


def test_the_result_survives_its_scene(scene: ts.Scene) -> None:
    rays = ts.simulate(scene)
    expected = rays.copy()
    del scene
    assert np.array_equal(rays, expected)


def test_point_and_rect_sources_also_run(scene: ts.Scene) -> None:
    scene.source = ts.PointSource(
        type="PointSource",
        divergence=ts.Deg(type="Deg", value=1.5),
        energy=ts.ElectronVolt(type="ElectronVolt", eV=100.0),
    )
    assert ts.simulate(scene).shape == (32, 7)

    scene.source = ts.RectSource(
        type="RectSource",
        width=2.0,
        height=1.0,
        energy=ts.ElectronVolt(type="ElectronVolt", eV=100.0),
    )
    rays = ts.simulate(scene)
    assert rays.shape == (32, 7)
    assert np.abs(rays[:, 0] - 0.0).max() <= 1.0  # inside the source extent


def test_defaults_come_from_the_cpp_model() -> None:
    # The defaults in the generated models are the C++ defaults, carried across
    # by schema/model_facts.json.
    source = ts.PointSource(
        type="PointSource", energy=ts.ElectronVolt(type="ElectronVolt", eV=1.0)
    )
    assert source.divergence == ts.Rad(type="Rad", value=0.0)

    element = ts.Element(
        name="e",
        area=ts.RectArea(type="RectArea", width=1.0, height=1.0),
        behavior=ts.Mirror(type="Mirror"),
    )
    assert element.position == [0.0, 0.0, 0.0]
    assert element.normal == [0.0, 0.0, 1.0]
    assert element.behavior.reflectivity == 1.0
    assert ts.Grating(type="Grating", lineDensity=300.0).order == 1

    scene = ts.Scene(name="s", source=source, elements=[])
    assert scene.numRays == 10_000
    assert scene.seed is None


def test_cpp_round_trip_preserves_the_scene() -> None:
    once = ts._core.scene_to_json(json.dumps(raw_scene()), raw_buffers())
    twice = ts._core.scene_to_json(once, raw_buffers())
    assert once == twice

    reparsed = json.loads(once)
    assert reparsed["name"] == "fixture"
    assert reparsed["numRays"] == 4
    assert reparsed["seed"] == 99
    # dvec3 fields survive as [x, y, z] ...
    assert reparsed["elements"][0]["position"] == [0.0, 0.0, 1000.0]
    assert reparsed["elements"][0]["normal"] == [0.0, 0.0, 1.0]
    # ... and arrays as buffer references, renumbered in traversal order.
    assert reparsed["source"]["positions"] == {
        "$buffer": 0,
        "dtype": "float64",
        "shape": [2, 3],
    }
    assert reparsed["source"]["weights"]["$buffer"] == 1


def test_a_raw_document_simulates_too() -> None:
    assert simulate_raw(raw_scene()).shape == (4, 7)
