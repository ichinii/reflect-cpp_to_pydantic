# GENERATED FILE -- DO NOT EDIT.
#
# Produced from schema/scene.pydantic.schema.json by scripts/generate_models.sh,
# which derives that schema from the C++ definition in
# cpp/include/toyscene/Scene.h. Edit the C++ and regenerate; edits made here are
# lost on the next run and will fail tests/test_schema_drift.py in the meantime.

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from toyscene._arrays import Float64Array1D, Float64Array2D
from toyscene._base import SceneModel


class Deg(SceneModel):
    type: Literal['Deg']
    value: Annotated[
        float, Field(ge=-1.7976931348623157e308, le=1.7976931348623157e308)
    ]


class Detector(SceneModel):
    type: Literal['Detector']


class ElectronVolt(SceneModel):
    type: Literal['ElectronVolt']
    eV: Annotated[
        float, Field(ge=-1.7976931348623157e308, gt=0.0, le=1.7976931348623157e308)
    ]


class EllipseArea(SceneModel):
    type: Literal['EllipseArea']
    a: Annotated[
        float, Field(ge=-1.7976931348623157e308, gt=0.0, le=1.7976931348623157e308)
    ]
    b: Annotated[
        float, Field(ge=-1.7976931348623157e308, gt=0.0, le=1.7976931348623157e308)
    ]


class Grating(SceneModel):
    type: Literal['Grating']
    lineDensity: Annotated[
        float, Field(ge=-1.7976931348623157e308, gt=0.0, le=1.7976931348623157e308)
    ]
    order: int = 1


class Mirror(SceneModel):
    type: Literal['Mirror']
    reflectivity: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0


class Rad(SceneModel):
    type: Literal['Rad']
    value: Annotated[
        float, Field(ge=-1.7976931348623157e308, le=1.7976931348623157e308)
    ]


class RectArea(SceneModel):
    type: Literal['RectArea']
    width: Annotated[
        float, Field(ge=-1.7976931348623157e308, gt=0.0, le=1.7976931348623157e308)
    ]
    height: Annotated[
        float, Field(ge=-1.7976931348623157e308, gt=0.0, le=1.7976931348623157e308)
    ]


class Wavelength(SceneModel):
    type: Literal['Wavelength']
    nm: Annotated[
        float, Field(ge=-1.7976931348623157e308, gt=0.0, le=1.7976931348623157e308)
    ]


Angle = Annotated[Deg | Rad, Field(discriminator='type')]

Area = Annotated[RectArea | EllipseArea, Field(discriminator='type')]

Behavior = Annotated[Mirror | Detector | Grating, Field(discriminator='type')]


class Element(SceneModel):
    name: Annotated[str, Field(min_length=1)]
    position: Annotated[list[float], Field(max_length=3, min_length=3)] = [
        0.0,
        0.0,
        0.0,
    ]
    normal: Annotated[list[float], Field(max_length=3, min_length=3)] = [0.0, 0.0, 1.0]
    area: Area
    behavior: Behavior


PhotonEnergy = Annotated[Wavelength | ElectronVolt, Field(discriminator='type')]


class PointSource(SceneModel):
    type: Literal['PointSource']
    divergence: Angle = {'type': 'Rad', 'value': 0.0}
    energy: PhotonEnergy


class RectSource(SceneModel):
    type: Literal['RectSource']
    width: Annotated[float, Field(ge=0.0, le=1.7976931348623157e308)]
    height: Annotated[float, Field(ge=0.0, le=1.7976931348623157e308)]
    energy: PhotonEnergy


class SampledSource(SceneModel):
    type: Literal['SampledSource']
    positions: Float64Array2D
    weights: Float64Array1D | None = None
    energy: PhotonEnergy


Source = Annotated[PointSource | RectSource | SampledSource, Field(discriminator='type')]


class Scene(SceneModel):
    name: Annotated[str, Field(min_length=1)]
    numRays: Annotated[int, Field(ge=1, le=10000000)] = 10000
    seed: int | None = None
    source: Source
    elements: list[Element]
