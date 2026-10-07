"""Shared scene fixtures.

Two shapes of the same scene are available: `scene` as Pydantic models, and
`raw_scene()` as the plain dict the models serialize to. Tests use the first to
check what Pydantic rejects and the second to check what C++ rejects on its own,
by handing the document to `toyscene._core.simulate_json` directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import toyscene as ts

ROWS = 64

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def positions() -> np.ndarray:
    rng = np.random.default_rng(20261007)
    return rng.normal(scale=0.5, size=(ROWS, 3))


@pytest.fixture
def weights() -> np.ndarray:
    rng = np.random.default_rng(7)
    return rng.random(ROWS)


@pytest.fixture
def scene(positions: np.ndarray, weights: np.ndarray) -> ts.Scene:
    return ts.Scene(
        name="fixture",
        numRays=32,
        seed=99,
        source=ts.SampledSource(
            type="SampledSource",
            positions=positions,
            weights=weights,
            energy=ts.ElectronVolt(type="ElectronVolt", eV=250.0),
        ),
        elements=[
            ts.Element(
                name="mirror",
                position=[0.0, 0.0, 1000.0],
                normal=[0.0, 0.0, 1.0],
                area=ts.RectArea(type="RectArea", width=20.0, height=4.0),
                behavior=ts.Mirror(type="Mirror", reflectivity=0.9),
            ),
            ts.Element(
                name="detector",
                position=[0.0, 0.0, 2000.0],
                area=ts.EllipseArea(type="EllipseArea", a=5.0, b=3.0),
                behavior=ts.Detector(type="Detector"),
            ),
        ],
    )


def raw_scene(**overrides: Any) -> dict[str, Any]:
    """The same scene as a plain document, for bypassing Pydantic.

    Buffer 0 is a (2, 3) positions array, buffer 1 a (2,) weights array.
    """
    document: dict[str, Any] = {
        "name": "fixture",
        "numRays": 4,
        "seed": 99,
        "source": {
            "type": "SampledSource",
            "positions": {"$buffer": 0, "dtype": "float64", "shape": [2, 3]},
            "weights": {"$buffer": 1, "dtype": "float64", "shape": [2]},
            "energy": {"type": "ElectronVolt", "eV": 250.0},
        },
        "elements": [
            {
                "name": "mirror",
                "position": [0.0, 0.0, 1000.0],
                "normal": [0.0, 0.0, 1.0],
                "area": {"type": "RectArea", "width": 20.0, "height": 4.0},
                "behavior": {"type": "Mirror", "reflectivity": 0.9},
            }
        ],
    }
    document.update(overrides)
    return document


def raw_buffers() -> list[np.ndarray]:
    return [np.zeros((2, 3)), np.array([1.0, 3.0])]


#: Placed in a raw document, this becomes the bare JSON number literal 1e999 --
#: syntactically a number, but not representable as a double. `json.dumps` would
#: otherwise write `Infinity`, which is not JSON at all.
OVERFLOW = "<<1e999>>"


def simulate_raw(document: dict[str, Any], buffers: list[np.ndarray] | None = None):
    """Hand a document straight to C++, with no Pydantic in the way."""
    text = json.dumps(document).replace(f'"{OVERFLOW}"', "1e999")
    return ts._core.simulate_json(text, raw_buffers() if buffers is None else buffers)
