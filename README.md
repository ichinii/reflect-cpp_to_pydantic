# reflect-cpp_to_pydantic

A prototype of one architecture: **the data model is defined once, in C++**, and
everything else is derived from it. The library inside is `toyscene`, a toy
optical scene description.

```
cpp/include/toyscene/Scene.h          plain aggregate structs, rfl::Validator guards,
       |                              rfl::TaggedUnion variants, glm::dvec3
       |  reflect-cpp
       +--> JSON parsing + validation           (cpp/src/Serialization.cpp)
       +--> schema/scene.schema.json            (cpp/tools/export_schema.cpp)
       +--> schema/model_facts.json             what that schema cannot express
       +--> schema/named_unions.json            the unions reflect-cpp inlines
                |  scripts/generate_models.sh
                +--> python/toyscene/_models.py  Pydantic v2 models, generated
                                                 and committed, never hand-written
```

Scenes are built in Python, validated there, and cross as JSON. Bulk arrays
travel alongside as numpy buffers rather than inside the JSON, so nothing large
is copied either way; results come back as a `(numRays, 7)` float64 array Python
owns without a copy.

reflect-cpp → JSON Schema → datamodel-code-generator → Pydantic v2, with
nanobind and numpy at the boundary, glm for vectors, and nix, CMake and
scikit-build-core for the build.

## One field, end to end

`Mirror` — the smallest struct that touches every stage.

**1. Written by hand.** `Scene.h`, alias from `Types.h`:

```cpp
using UnitIntervalDouble =
    rfl::Validator<double, rfl::Minimum<0.0>, rfl::Maximum<1.0>, Finite>;

struct Mirror {
  UnitIntervalDouble reflectivity = 1.0;
};

using Behavior = rfl::TaggedUnion<"type", Mirror, Detector, Grating>;
```

A plain aggregate — no macros, no annotations, no IDL. The field *types* carry
the metadata: `rfl::Validator` the bounds, `rfl::TaggedUnion` the variant set.

**2. reflect-cpp exports it.** `schema/scene.schema.json`, abridged:

```json
"toyscene__Mirror__tagged": {
  "type": "object",
  "properties": {
    "type": { "type": "string", "enum": ["Mirror"] },
    "reflectivity": {
      "allOf": [
        { "minimum": 0.0, "type": "number" },
        { "maximum": 1.0, "type": "number" },
        { "allOf": [ { "minimum": -1.7976931348623157e308, "type": "number" },
                     { "maximum":  1.7976931348623157e308, "type": "number" } ] }
      ]
    }
  },
  "required": ["type", "reflectivity"]
},
"toyscene__Element": {
  "properties": {
    "behavior": {
      "anyOf": [
        { "$ref": "#/$defs/toyscene__Mirror__tagged" },
        { "$ref": "#/$defs/toyscene__Detector__tagged" },
        { "$ref": "#/$defs/toyscene__Grating__tagged" }
      ]
    }
  }
}
```

The bound survived, but four things block code generation:

* the `$defs` key is a mangled C++ type name
* the composed validator arrives as a *nested* `allOf` of single-keyword objects
* `reflectivity` sits in `required` with no default — reflect-cpp's schema has no
  node for one
* the union has no name: an anonymous `anyOf` at every use site, no discriminator

**3. Post-processing closes exactly those.** `scripts/postprocess_schema.py` →
`schema/scene.pydantic.schema.json`:

```json
"Mirror": {
  "type": "object",
  "properties": {
    "type": { "type": "string", "enum": ["Mirror"] },
    "reflectivity": { "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 1.0 }
  },
  "required": ["type"]
},
"Behavior": {
  "anyOf": [ { "$ref": "#/$defs/Mirror" },
             { "$ref": "#/$defs/Detector" },
             { "$ref": "#/$defs/Grating" } ],
  "discriminator": { "propertyName": "type" }
}
```

* name cleaned
* `allOf` flattened, keeping the tightest bound per keyword — which retires the
  `±DBL_MAX` pair the `Finite` rule is forced to emit
* `default: 1.0` injected from `model_facts.json`; `reflectivity` leaves `required`
* `anyOf` hoisted into a named definition, discriminator added

Nothing is invented along the way: the default is read off a real
`Mirror{}.reflectivity`, the union schema comes from
`rfl::json::to_schema<Behavior>()`.

**4. Generated.** `python/toyscene/_models.py`, committed:

```python
class Mirror(SceneModel):
    type: Literal['Mirror']
    reflectivity: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0

Behavior = Annotated[Mirror | Detector | Grating, Field(discriminator='type')]
```

`[0, 1]` was written once, as a type in a C++ header. Both sides now reject
`reflectivity=1.5`, `Behavior` is a name you can annotate with, and a variant
value *is* the variant — `isinstance(el.behavior, ts.Mirror)`, nothing to unpack.

Why each step is necessary: `CLAUDE.md`.

## Using it

```python
import numpy as np
import toyscene as ts

scene = ts.Scene(
    name="ring source",
    numRays=20_000,
    seed=4,
    source=ts.SampledSource(
        type="SampledSource",
        positions=np.zeros((100_000, 3)),      # the blob
        weights=np.ones(100_000),
        energy=ts.Wavelength(type="Wavelength", nm=1.24),
    ),
    elements=[
        ts.Element(
            name="mirror",
            position=[0.0, 0.0, 1000.0],
            normal=[0.0, 0.0349, 0.99939],
            area=ts.RectArea(type="RectArea", width=40.0, height=10.0),
            behavior=ts.Mirror(type="Mirror", reflectivity=0.84),
        ),
    ],
)
rays = ts.simulate(scene)                       # (20000, 7) float64
```

`examples/basic.py` runs this, plus three errors caught at three different places.

## Build

Everything happens inside the nix devShell, which pins the toolchain; `flake.nix`
is the authoritative list.

```sh
nix develop                              # creates and activates .venv on entry
pip install --no-build-isolation -e .     # builds toyscene._core via scikit-build-core
python -m pytest -q                       # Python tests
cmake -S . -B build-cpp && cmake --build build-cpp && ctest --test-dir build-cpp
python examples/basic.py
```

* Use `python -m pytest`, not `pytest` — the one on `PATH` comes from the nix
  environment and does not see the `.venv`, which exists because nixpkgs'
  `site-packages` is read-only and an editable install needs somewhere to write.
* reflect-cpp is **not in nixpkgs**, so CMake fetches it with `FetchContent` at
  the pinned tag `v0.25.0` (into `.deps`, overridable with
  `-DFETCHCONTENT_SOURCE_DIR_REFLECTCPP=...`). Everything else comes from nixpkgs.

## Regenerating the models

```sh
./scripts/generate_models.sh
```

| file | produced by | content |
| --- | --- | --- |
| `schema/scene.schema.json` | `cpp/tools/export_schema.cpp` | `rfl::json::to_schema<Scene>()`, verbatim |
| `schema/model_facts.json` | same | the few things that schema cannot express |
| `schema/named_unions.json` | same, from `cpp/src/NamedTypes.h` | `rfl::json::to_schema<T>()` per registered union |
| `schema/scene.pydantic.schema.json` | `scripts/postprocess_schema.py` | input for the code generator |
| `python/toyscene/_models.py` | datamodel-code-generator + `scripts/postprocess_models.py` | the Pydantic models |

All five are committed. `tests/test_schema_drift.py` reruns the pipeline into a
temporary directory and fails if any committed file is stale — including a check
that the *compiled* extension agrees, so a stale build cannot make the other
tests lie.

## What is checked where

| | checked by | examples |
| --- | --- | --- |
| **value ranges** | Pydantic **and** C++ | `reflectivity` in [0, 1], `numRays` in [1, 10'000'000], non-empty `name`, positive energies |
| **shape of the data** | Pydantic (rank) and C++ (rank + extent) | `positions` is 2-d; `positions` is (N, 3) |
| **cross-field and non-schema rules** | C++ only | as many `weights` as `positions` rows, non-negative weights, finite bulk data, `|normal| == 1` |

Range constraints are written once, as `rfl::Validator` aliases in
`cpp/include/toyscene/Types.h`, and reach Python through the schema. The third
row cannot be expressed in JSON Schema at all; those rules live in `validate()`
methods and surface as `toyscene.SceneError` (a `ValueError`) with the JSON path:

```
source.weights: has 50000 entries, but positions has 100000 rows
elements[0].normal: expected a unit vector, but |normal| = 1.044031
```

## Named tagged unions

`Angle`, `PhotonEnergy`, `Source`, `Area` and `Behavior` are names in the
generated Python, usable in annotations of your own:

```python
Angle = Annotated[Deg | Rad, Field(discriminator='type')]

class PointSource(SceneModel):
    divergence: Angle = {'type': 'Rad', 'value': 0.0}
    energy: PhotonEnergy
```

reflect-cpp emits no definition for a union, so `cpp/src/NamedTypes.h` is a
registry of the aliases that should get one. To add one:

* a line in `namedUnions()` — `namedUnion<Polarization>("Polarization"),`
* `./scripts/generate_models.sh`
* the name in the re-export list in `python/toyscene/__init__.py`

The Python-facing name is the only hand-written part; the schema comes from the
C++ type. A stale registry fails the build — see `CLAUDE.md` for the failure modes.

## Known limits

* The simulation is a placeholder: it reacts to the scene, but the elements do
  nothing physical.
* A field with a C++ default is optional in Python, required in the JSON. Pydantic
  fills defaults before serializing, so C++ only sees complete documents; a
  hand-written document passed to `_core.simulate_json` must spell every field out.
* Only `float64` arrays exist — `dtypeName<T>()` is specialized for `double` alone.
* `toJson` renumbers buffer references, so a round trip preserves the scene but
  not the original buffer indices.
* `Array<T>` from `fromJson` is a view into the caller's buffers; a `Scene` that
  outlives the call would need `BufferView::owner` set.
* `fromJson`/`toJson` use `thread_local` buffer tables — safe across threads, but
  an `Array<T>` cannot be parsed outside one of those scopes.

## Layout

```
cpp/include/toyscene/    public headers: Scene.h, Types.h, Array.h,
                         Serialization.h, Simulate.h  (reflect-cpp + glm allowed)
cpp/src/NamedTypes.h     private: the registry of tagged unions to name
cpp/src/Reflectors.h     private: rfl::Reflector for glm::dvec3 and Array<T>
cpp/src/Serialization.cpp  fromJson / toJson / jsonSchema, validate(), buffer table
cpp/src/Simulate.cpp     the dummy simulation
cpp/tools/export_schema.cpp  writes the three exported schema files
cpp/tests/test_scene.cpp     the ctest target
bindings/module.cpp      nanobind module toyscene._core
python/toyscene/__init__.py  public API: models, simulate(), SceneError
python/toyscene/_arrays.py   ArraySpec: numpy <-> buffer reference
python/toyscene/_base.py     shared model config for the generated classes
python/toyscene/_models.py   GENERATED, committed
scripts/                 generate_models.sh and its two post-processing steps
tests/                   pytest suite
examples/basic.py
```
