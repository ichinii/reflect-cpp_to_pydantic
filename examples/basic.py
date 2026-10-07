#!/usr/bin/env python3
"""A scene with a 100k-point sampled source, and three ways to get it wrong.

Run inside the nix devShell, after `pip install --no-build-isolation -e .`:

    python examples/basic.py
"""

from __future__ import annotations

import math
import textwrap

import numpy as np
from pydantic import ValidationError

import toyscene as ts

N = 100_000


def rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


def show_error(title: str, caught_by: str, error: Exception) -> None:
    print(f"\n{title}")
    print(f"  caught by: {caught_by}")
    print(f"  {type(error).__module__}.{type(error).__name__}:")
    print(textwrap.indent(str(error).strip(), "    "))


def tilted_normal(about_x: ts.Angle) -> list[float]:
    """A unit normal tilted away from +z by `about_x`, about the x axis.

    The parameter annotation is half the point of this function: `Angle` exists
    as a name in Python only because the C++ alias is registered in
    `cpp/src/NamedTypes.h` -- reflect-cpp inlines a tagged union at every use
    site and gives it no definition of its own. The other half is that the
    argument really is a `Deg` or a `Rad`, with no wrapper to unpack.
    """
    radians = math.radians(about_x.value) if isinstance(about_x, ts.Deg) else about_x.value
    return [0.0, math.sin(radians), math.cos(radians)]


def build_scene() -> ts.Scene:
    rng = np.random.default_rng(20261007)

    # The blob: a ring of source points, with a weight per point.
    angle = rng.uniform(0.0, 2.0 * math.pi, N)
    radius = 2.0 + 0.1 * rng.normal(size=N)
    positions = np.column_stack(
        [radius * np.cos(angle), radius * np.sin(angle), np.zeros(N)]
    )
    weights = np.exp(-((radius - 2.0) ** 2) / 0.02)

    # A mirror tilted by 2 degrees about x. The normal has to be a unit vector;
    # C++ checks that, and nothing in the JSON Schema could.
    normal = tilted_normal(ts.Deg(type="Deg", value=2.0))

    return ts.Scene(
        name="ring source through a mirror",
        numRays=20_000,
        seed=4,
        source=ts.SampledSource(
            type="SampledSource",
            positions=positions,
            weights=weights,
            energy=ts.Wavelength(type="Wavelength", nm=1.24),
        ),
        elements=[
            ts.Element(
                name="mirror",
                position=[0.0, 0.0, 1000.0],
                normal=normal,
                area=ts.RectArea(type="RectArea", width=40.0, height=10.0),
                behavior=ts.Mirror(type="Mirror", reflectivity=0.84),
            ),
            ts.Element(
                name="detector",
                position=[0.0, 20.0, 2000.0],
                area=ts.EllipseArea(type="EllipseArea", a=12.0, b=6.0),
                behavior=ts.Detector(type="Detector"),
            ),
        ],
    )


def main() -> None:
    scene = build_scene()

    rule("the scene")
    print(f"  name      {scene.name}")
    print(f"  numRays   {scene.numRays:,}")
    print(f"  seed      {scene.seed}")
    print(f"  source    {scene.source.type}, positions{scene.source.positions.shape}"
          f" ({scene.source.positions.nbytes / 1e6:.1f} MB, passed out of band)")
    print(f"  energy    {scene.source.energy.type} {scene.source.energy.nm} nm")
    for element in scene.elements:
        print(f"  element   {element.name:<9} {element.behavior.type:<9}"
              f" at {element.position} normal {np.round(element.normal, 4).tolist()}")
    # The tagged unions are named aliases, so a variant value is the variant.
    print(f"  source is a {type(scene.source).__name__}, "
          f"its energy a {type(scene.source.energy).__name__} "
          f"(ts.Source and ts.PhotonEnergy are aliases, not wrappers)")

    rule("simulate")
    rays = ts.simulate(scene)
    print(f"  shape     {rays.shape}")
    print(f"  dtype     {rays.dtype}")
    print(f"  owns data {rays.flags.owndata}   <- handed over, not copied")
    print(f"  columns   x, y, z, dx, dy, dz, energy_eV")
    print(f"  origin x  mean {rays[:, 0].mean():+.4f}  std {rays[:, 0].std():.4f}")
    print(f"  origin y  mean {rays[:, 1].mean():+.4f}  std {rays[:, 1].std():.4f}")
    offset = np.array([rays[:, 0].mean(), rays[:, 1].mean()])
    print(f"  ring r    mean {np.hypot(*(rays[:, :2] - offset).T).mean():.4f}"
          f"  (the source ring sits at r = 2.0)")
    print(f"  energy    {rays[0, 6]:.2f} eV  (from {scene.source.energy.nm} nm)")
    print(f"  identical on a rerun: {np.array_equal(rays, ts.simulate(scene))}")

    rule("three ways to get it wrong")

    # 1. A range the schema carries, so Pydantic catches it before anything is
    #    serialized.
    try:
        ts.Mirror(type="Mirror", reflectivity=1.5)
    except ValidationError as error:
        show_error(
            "1. reflectivity = 1.5",
            "pydantic, at model construction (the bound comes from "
            "UnitIntervalDouble in Types.h)",
            error,
        )

    # 2. A cross-field rule: no JSON Schema can say "as many weights as rows".
    broken = build_scene()
    broken.source.weights = broken.source.weights[: N // 2]
    try:
        ts.simulate(broken)
    except ts.SceneError as error:
        show_error(
            "2. half as many weights as positions",
            "C++ only, cross-field (SampledSource::validate)",
            error,
        )

    # 3. A non-schema rule: nor can it say "this vector has length 1".
    broken = build_scene()
    broken.elements[0].normal = [0.0, 0.3, 1.0]
    try:
        ts.simulate(broken)
    except ts.SceneError as error:
        show_error(
            "3. normal that is not a unit vector",
            "C++ only, non-schema rule (Element::validate)",
            error,
        )

    print()


if __name__ == "__main__":
    main()
