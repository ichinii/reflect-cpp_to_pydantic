"""Bulk arrays: the out-of-band path and everything that can go wrong on it."""

from __future__ import annotations

import json

import numpy as np
import pytest
from pydantic import ValidationError

import toyscene as ts
from conftest import raw_scene, simulate_raw


def test_arrays_are_normalised_on_construction() -> None:
    source = ts.SampledSource(
        type="SampledSource",
        positions=[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]],  # a nested list is fine
        energy=ts.ElectronVolt(type="ElectronVolt", eV=1.0),
    )
    assert isinstance(source.positions, np.ndarray)
    assert source.positions.dtype == np.float64
    assert source.positions.flags.c_contiguous


def test_a_non_contiguous_array_is_copied_on_the_way_in() -> None:
    # ArraySpec normalises on validation, so the model always holds something
    # the extension can read in place. (The extension itself rejects a
    # non-contiguous buffer rather than converting it -- see below.)
    view = np.zeros((4, 6))[:, ::2]
    assert not view.flags.c_contiguous
    source = ts.SampledSource(
        type="SampledSource",
        positions=view,
        energy=ts.ElectronVolt(type="ElectronVolt", eV=1.0),
    )
    assert source.positions.flags.c_contiguous


@pytest.mark.parametrize("value", [np.zeros(6), np.zeros((2, 3, 1))])
def test_pydantic_rejects_the_wrong_rank(value: np.ndarray) -> None:
    with pytest.raises(ValidationError, match="expected a 2-dimensional array"):
        ts.SampledSource(
            type="SampledSource",
            positions=value,
            energy=ts.ElectronVolt(type="ElectronVolt", eV=1.0),
        )


def test_pydantic_rejects_the_wrong_rank_for_weights() -> None:
    with pytest.raises(ValidationError, match="expected a 1-dimensional array"):
        ts.SampledSource(
            type="SampledSource",
            positions=np.zeros((2, 3)),
            weights=np.zeros((2, 1)),
            energy=ts.ElectronVolt(type="ElectronVolt", eV=1.0),
        )


def test_the_row_width_of_positions_is_a_cpp_only_rule(scene: ts.Scene) -> None:
    # (N, 2) is a perfectly good 2-d array as far as the schema is concerned.
    scene.source.positions = np.zeros((8, 2))
    scene.source.weights = np.ones(8)
    with pytest.raises(ts.SceneError, match=r"source.positions: expected shape \(N, 3\)"):
        ts.simulate(scene)


def test_mismatched_weights_are_a_cpp_only_rule(scene: ts.Scene) -> None:
    scene.source.weights = np.ones(7)
    with pytest.raises(ts.SceneError) as caught:
        ts.simulate(scene)
    assert "source.weights" in str(caught.value)
    assert "7 entries" in str(caught.value)


def test_negative_weights_are_a_cpp_only_rule(scene: ts.Scene) -> None:
    scene.source.weights = scene.source.weights.copy()
    scene.source.weights[3] = -1.0
    with pytest.raises(ts.SceneError, match=r"source.weights: every value must be >= 0"):
        ts.simulate(scene)


def test_weights_summing_to_zero_are_rejected(scene: ts.Scene) -> None:
    scene.source.weights = np.zeros_like(scene.source.weights)
    with pytest.raises(ts.SceneError, match="must not sum to zero"):
        ts.simulate(scene)


def test_non_finite_bulk_data_is_a_cpp_only_rule(scene: ts.Scene) -> None:
    # This is the path NaN can actually take into a scene: a numpy buffer never
    # passes through a JSON number, so nothing rejects it earlier.
    scene.source.positions = scene.source.positions.copy()
    scene.source.positions[2, 1] = np.nan
    with pytest.raises(ts.SceneError, match=r"source.positions: every value must be finite"):
        ts.simulate(scene)

    scene.source.positions[2, 1] = 0.0
    scene.source.weights = scene.source.weights.copy()
    scene.source.weights[0] = np.inf
    with pytest.raises(ts.SceneError, match=r"source.weights: every value must be finite"):
        ts.simulate(scene)


def test_empty_positions_are_rejected(scene: ts.Scene) -> None:
    scene.source.positions = np.zeros((0, 3))
    scene.source.weights = np.zeros(0)
    with pytest.raises(ts.SceneError, match="at least one row"):
        ts.simulate(scene)


def test_weights_actually_steer_the_sampling() -> None:
    positions = np.array([[0.0, 0.0, 0.0], [100.0, 0.0, 0.0]])
    common = dict(name="weighted", numRays=400, seed=5, elements=[])
    energy = ts.ElectronVolt(type="ElectronVolt", eV=1.0)

    first = ts.simulate(
        ts.Scene(
            source=ts.SampledSource(
                type="SampledSource", positions=positions,
                weights=np.array([1.0, 0.0]), energy=energy,
            ),
            **common,
        )
    )
    second = ts.simulate(
        ts.Scene(
            source=ts.SampledSource(
                type="SampledSource", positions=positions,
                weights=np.array([0.0, 1.0]), energy=energy,
            ),
            **common,
        )
    )
    assert np.allclose(first[:, 0], 0.0)
    assert np.allclose(second[:, 0], 100.0)


# --- the buffer protocol itself -------------------------------------------


def test_a_buffer_of_the_wrong_dtype_is_rejected() -> None:
    with pytest.raises(ts.SceneError, match="instead of 'float64'"):
        simulate_raw(raw_scene(), [np.zeros((2, 3), dtype=np.float32), np.ones(2)])


def test_a_declared_dtype_the_field_cannot_hold_is_rejected() -> None:
    document = raw_scene()
    document["source"]["positions"]["dtype"] = "float32"
    with pytest.raises(ts.SceneError, match="this field holds 'float64'"):
        simulate_raw(document)


def test_a_declared_shape_that_disagrees_with_the_buffer_is_rejected() -> None:
    document = raw_scene()
    document["source"]["positions"]["shape"] = [3, 2]
    with pytest.raises(ts.SceneError, match="declares shape"):
        simulate_raw(document)


def test_an_out_of_range_buffer_index_is_rejected() -> None:
    document = raw_scene()
    document["source"]["positions"]["$buffer"] = 9
    with pytest.raises(ts.SceneError, match=r"only 2 buffer\(s\)"):
        simulate_raw(document)


def test_a_missing_buffer_list_is_rejected() -> None:
    with pytest.raises(ts.SceneError, match=r"only 0 buffer\(s\)"):
        simulate_raw(raw_scene(), [])


def test_the_extension_refuses_to_convert_a_non_contiguous_buffer() -> None:
    # .noconvert() on the argument: nanobind rejects the array instead of
    # silently materialising a contiguous copy of it.
    view = np.zeros((2, 6))[:, ::2]
    assert not view.flags.c_contiguous
    with pytest.raises(TypeError, match="incompatible function arguments"):
        simulate_raw(raw_scene(), [view, np.ones(2)])


def test_serializing_without_a_buffer_sink_explains_itself(scene: ts.Scene) -> None:
    with pytest.raises(Exception, match="buffer sink"):
        scene.model_dump_json()


def test_buffers_are_collected_in_reference_order(scene: ts.Scene) -> None:
    buffers: list[np.ndarray] = []
    document = json.loads(scene.model_dump_json(context={"buffers": buffers}))
    assert len(buffers) == 2
    assert document["source"]["positions"]["$buffer"] == 0
    assert document["source"]["weights"]["$buffer"] == 1
    assert buffers[0].shape == (64, 3)
    assert buffers[1].shape == (64,)
    # The buffers are the model's own arrays, not copies of them.
    assert buffers[0] is scene.source.positions


def test_a_buffer_reference_cannot_be_passed_back_in() -> None:
    with pytest.raises(ValidationError, match="buffer reference"):
        ts.SampledSource(
            type="SampledSource",
            positions={"$buffer": 0, "dtype": "float64", "shape": [2, 3]},
            energy=ts.ElectronVolt(type="ElectronVolt", eV=1.0),
        )
