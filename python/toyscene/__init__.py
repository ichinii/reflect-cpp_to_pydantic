"""toyscene: a scene description defined once, in C++.

The models re-exported here are generated from the C++ definition in
`cpp/include/toyscene/Scene.h` (see `scripts/generate_models.sh`). Build a
scene out of them and hand it to `simulate`:

    import numpy as np
    import toyscene as ts

    scene = ts.Scene(
        name="demo",
        numRays=1000,
        seed=7,
        source=ts.SampledSource(
            type="SampledSource",
            positions=np.zeros((128, 3)),
            energy=ts.ElectronVolt(type="ElectronVolt", eV=250.0),
        ),
        elements=[
            ts.Element(
                name="detector",
                position=[0.0, 0.0, 1000.0],
                area=ts.EllipseArea(type="EllipseArea", a=5.0, b=5.0),
                behavior=ts.Detector(type="Detector"),
            )
        ],
    )
    rays = ts.simulate(scene)      # (1000, 7) float64

The tagged unions are re-exported as type aliases -- `Angle`, `PhotonEnergy`,
`Source`, `Area`, `Behavior` -- so they can be used in annotations of your own:

    def tilt(divergence: ts.Angle) -> ts.PointSource: ...

Value ranges are checked twice: by Pydantic when the model is built, and again
by C++ when the document is parsed. Rules that a JSON Schema cannot carry --
cross-field constraints, unit-length normals, finiteness of bulk data -- are
checked only by C++ and surface as `SceneError`.
"""

from __future__ import annotations

import numpy as np

from . import _core
from ._arrays import ArraySpec, Float64Array1D, Float64Array2D
from ._models import (
    # the variant members
    Deg,
    Detector,
    ElectronVolt,
    EllipseArea,
    Element,
    Grating,
    Mirror,
    PointSource,
    Rad,
    RectArea,
    RectSource,
    SampledSource,
    Scene,
    Wavelength,
)
from ._models import (
    # the tagged unions, as discriminated type aliases rather than classes, so
    # `scene.source` is a PointSource/RectSource/SampledSource and not a wrapper
    # around one. Registered in cpp/src/NamedTypes.h.
    Angle,
    Area,
    Behavior,
    PhotonEnergy,
    Source,
)

#: Raised for anything the C++ side rejects. A `ValueError`, so it can be
#: caught together with `pydantic.ValidationError`.
SceneError = _core.SceneError

#: Columns of a result row: x, y, z, dx, dy, dz, energy_eV.
RESULT_COLUMNS = _core.RESULT_COLUMNS


def simulate(scene: Scene) -> np.ndarray:
    """Trace `scene.numRays` rays and return a `(numRays, 7)` float64 array.

    The returned array does not own its memory: it is the simulation's output
    buffer, handed over without a copy.

    Raises:
        SceneError: if C++ rejects the scene.
    """
    buffers: list[np.ndarray] = []
    document = scene.model_dump_json(context={"buffers": buffers})
    return _core.simulate_json(document, buffers)


def scene_json_schema() -> str:
    """The JSON Schema the Pydantic models were generated from, from C++."""
    return _core.json_schema()


__all__ = [
    "RESULT_COLUMNS",
    "Angle",
    "Area",
    "ArraySpec",
    "Behavior",
    "Deg",
    "Detector",
    "ElectronVolt",
    "EllipseArea",
    "Element",
    "Float64Array1D",
    "Float64Array2D",
    "Grating",
    "Mirror",
    "PhotonEnergy",
    "PointSource",
    "Rad",
    "RectArea",
    "RectSource",
    "SampledSource",
    "Scene",
    "SceneError",
    "Source",
    "Wavelength",
    "scene_json_schema",
    "simulate",
]
