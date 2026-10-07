# toyscene

A prototype of one architecture: **the scene description is defined once, in C++**,
and everything else is derived from it.

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

Python users build a scene out of the generated models and call `simulate(scene)`.
The scene crosses the boundary as JSON; bulk arrays do not travel inside that
JSON but alongside it as numpy buffers, so nothing large is copied in either
direction. The result comes back as a `(numRays, 7)` float64 array that Python
owns without a copy.

## What is actually happening

One struct, carried end to end. `Mirror` is three lines of C++ and it exercises
every stage of the pipeline at once.

### 1. What a human writes

This is the only hand-written artifact in the chain
(`cpp/include/toyscene/Scene.h`, with the alias from `cpp/include/toyscene/Types.h`):

```cpp
using UnitIntervalDouble =
    rfl::Validator<double, rfl::Minimum<0.0>, rfl::Maximum<1.0>, Finite>;

struct Mirror {
  UnitIntervalDouble reflectivity = 1.0;
};

using Behavior = rfl::TaggedUnion<"type", Mirror, Detector, Grating>;
```

A plain aggregate. No macros, no annotations, no separate IDL file — reflect-cpp
reflects over the struct at compile time, and the *types* of the fields are what
carries the metadata: `rfl::Validator` holds the value bounds, `rfl::TaggedUnion`
holds the variant set.

### 2. What reflect-cpp exports

`rfl::json::to_schema<Scene>()` turns that into JSON Schema
(`schema/scene.schema.json`, abridged here to `reflectivity` and the one use site):

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

The bound survived the trip, but four things are wrong for code generation: the
definition key is a mangled C++ type name; the composed validator arrives as a
*nested* `allOf` of single-keyword objects; `reflectivity` is in `required` with
no sign of the `= 1.0`, because reflect-cpp's schema has no node for a per-field
default at all; and `Behavior` has no name — a tagged union is inlined as an
anonymous `anyOf` at every use site, with no discriminator.

### 3. What post-processing makes of it

`scripts/postprocess_schema.py` closes exactly those gaps
(`schema/scene.pydantic.schema.json`):

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

Four edits, one per wart: the name is cleaned; the nested `allOf` is flattened,
keeping the tightest bound per keyword — which is what retires the `±DBL_MAX`
pair that the `Finite` rule is forced to emit; `default: 1.0` is injected from
`schema/model_facts.json` and `reflectivity` leaves `required`; and the inline
`anyOf` is hoisted into a named definition with a discriminator, so every use
site becomes `{"$ref": "#/$defs/Behavior"}`.

None of those edits invents a value. The default is read off a real
`Mirror{}.reflectivity` in C++, and the union schema comes from
`rfl::json::to_schema<Behavior>()` — the C++ stays the single source of truth.

### 4. What comes out

datamodel-code-generator reads that schema and
`scripts/postprocess_models.py` finishes the job (`python/toyscene/_models.py`,
generated and committed):

```python
class Mirror(SceneModel):
    type: Literal['Mirror']
    reflectivity: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0

Behavior = Annotated[Mirror | Detector | Grating, Field(discriminator='type')]
```

So: `[0, 1]` was written once, as a type in a C++ header. Both sides of the
boundary now reject `reflectivity=1.5` with a comparable message, `Behavior` is a
name you can annotate with, and a variant value *is* the variant —
`isinstance(el.behavior, ts.Mirror)`, no wrapper to unpack. Nobody wrote the
Python.

### The technologies at each arrow

| stage | technology | role |
| --- | --- | --- |
| `Scene.h` | **reflect-cpp** 0.25 | compile-time reflection over plain C++20 aggregates. `rfl::Validator` carries value bounds, `rfl::TaggedUnion` carries variants, both reach the schema |
| parse / serialize | reflect-cpp (+ `rfl::NoExtraFields`) | `fromJson` / `toJson` in `cpp/src/Serialization.cpp` |
| `schema/*.json` | **JSON Schema** | the interchange format; the only thing the Python side reads |
| post-processing | **Python** (`scripts/postprocess_*.py`) | closes the gaps reflect-cpp's export cannot state |
| `_models.py` | **datamodel-code-generator** 0.35 | JSON Schema → Pydantic v2 source |
| runtime | **Pydantic v2** | validation in Python, before anything crosses |
| the boundary | **nanobind** 2.9 | `toyscene._core`; the scene as JSON, bulk arrays as **numpy** buffers alongside it |
| geometry | **glm** | `glm::dvec3`, via a custom `rfl::Reflector` |
| build | **nix** devShell, **CMake**, **scikit-build-core** | pinned toolchain |

Why each post-processing step is necessary, and which reflect-cpp limitations
force them, is written up in `CLAUDE.md`.

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

See `examples/basic.py` for the whole thing, including three errors caught at
three different places.

## Build

Everything happens inside the nix devShell, which pins the whole toolchain —
gcc, cmake, ninja, glm, and a Python 3.12 with numpy, pydantic,
datamodel-code-generator, nanobind, scikit-build-core and pytest. `flake.nix` is
the authoritative list.

```sh
nix develop                              # creates and activates .venv on entry
pip install --no-build-isolation -e .     # builds toyscene._core via scikit-build-core
python -m pytest -q                       # Python tests
cmake -S . -B build-cpp && cmake --build build-cpp && ctest --test-dir build-cpp
python examples/basic.py
```

Two things to know:

* `nix develop` creates a `.venv` with `--system-site-packages` and activates it,
  because nixpkgs' `site-packages` is read-only and an editable install needs
  somewhere to write. Use `python -m pytest` rather than `pytest`: the `pytest` on
  `PATH` comes from the nix environment and does not see the venv.
* reflect-cpp **is not packaged in nixpkgs**, so CMake fetches it with
  `FetchContent` at the pinned tag `v0.25.0` (into `.deps`, overridable with
  `-DFETCHCONTENT_SOURCE_DIR_REFLECTCPP=...`). Everything else comes from nixpkgs.

## Regenerating the models

```sh
./scripts/generate_models.sh
```

Five files are generated and committed:

| file | produced by | content |
| --- | --- | --- |
| `schema/scene.schema.json` | `cpp/tools/export_schema.cpp` | `rfl::json::to_schema<Scene>()`, verbatim |
| `schema/model_facts.json` | same | the few things that schema cannot express |
| `schema/named_unions.json` | same, from `cpp/src/NamedTypes.h` | `rfl::json::to_schema<T>()` per registered tagged union |
| `schema/scene.pydantic.schema.json` | `scripts/postprocess_schema.py` | input for the code generator |
| `python/toyscene/_models.py` | datamodel-code-generator + `scripts/postprocess_models.py` | the Pydantic models |

`tests/test_schema_drift.py` runs the whole pipeline into a temporary directory
and fails if any committed file is stale, so a change to `Scene.h` that has not
been propagated cannot pass unnoticed. A fifth check makes sure the *compiled*
extension agrees with the committed schema, so a stale build cannot make the
other tests lie.

## What is checked where

Three layers, deliberately:

| | checked by | examples |
| --- | --- | --- |
| **value ranges** | Pydantic **and** C++ | `reflectivity` in [0, 1], `numRays` in [1, 10'000'000], non-empty `name`, positive energies |
| **shape of the data** | Pydantic (rank) and C++ (rank + extent) | `positions` is 2-d; `positions` is (N, 3) |
| **cross-field and non-schema rules** | C++ only | as many `weights` as `positions` rows, non-negative weights, finite bulk data, `|normal| == 1` |

A range constraint is written **once**, as an `rfl::Validator` alias in
`cpp/include/toyscene/Types.h`. It reaches the generated models through the
schema, which is why both sides reject `reflectivity = 1.5` with a comparable
message. The rules in the third row cannot be expressed in a JSON Schema at all;
they live in `validate()` methods on the C++ structs and surface in Python as
`toyscene.SceneError` (a `ValueError`) with the JSON field path in the message:

```
source.weights: has 50000 entries, but positions has 100000 rows
elements[0].normal: expected a unit vector, but |normal| = 1.044031
```

## Named tagged unions

`Angle`, `PhotonEnergy`, `Source`, `Area` and `Behavior` exist as names in the
generated Python:

```python
Angle = Annotated[Deg | Rad, Field(discriminator='type')]

class PointSource(SceneModel):
    divergence: Angle = {'type': 'Rad', 'value': 0.0}
    energy: PhotonEnergy
```

so they can be used in annotations of your own. They exist because
`cpp/src/NamedTypes.h` is a registry of the aliases that should be named;
reflect-cpp itself emits no definition for a union. To add one, write a line in
`namedUnions()`:

```cpp
namedUnion<Polarization>("Polarization"),
```

then run `./scripts/generate_models.sh` and add the name to the re-export list in
`python/toyscene/__init__.py`. The Python-facing name is the only thing written by
hand; the schema comes from the C++ type. Post-processing fails loudly rather than
silently producing a different shape — see `CLAUDE.md` for the failure modes.

## Known limits

* **The simulation is a placeholder.** It samples origins and directions from the
  source and folds element positions and normals into a single offset, so the
  output reacts to the scene; the elements do nothing physical.
* **A field with a C++ default is optional in Python, but required in the JSON.**
  Pydantic fills defaults in before serializing, so C++ only ever sees complete
  documents. A hand-written document passed straight to `_core.simulate_json` must
  spell out every non-optional field.
* **Only `float64` arrays exist.** `dtypeName<T>()` is specialized for `double`
  alone, so another element type is a one-line addition plus a `model_facts` entry.
* **`toJson` renumbers buffer references** in traversal order, so a round trip
  preserves the scene but not necessarily the original buffer indices.
* **`Array<T>` instances from `fromJson` are views** into the caller's buffers. A
  `Scene` that outlives the call would need `BufferView::owner` set.
* **`fromJson`/`toJson` use `thread_local` buffer tables.** Safe to call from
  several threads, but an `Array<T>` cannot be parsed outside one of those scopes.

## Layout

```
cpp/include/toyscene/    public headers: Scene.h, Types.h, Array.h,
                         Serialization.h, Simulate.h  (reflect-cpp + glm allowed)
cpp/src/NamedTypes.h     private: the registry of tagged unions to name
cpp/src/Reflectors.h     private: rfl::Reflector for glm::dvec3 and Array<T>
cpp/src/Serialization.cpp  fromJson / toJson / jsonSchema, validate(), buffer table
cpp/src/Simulate.cpp     the dummy simulation
cpp/tools/export_schema.cpp  writes schema/scene.schema.json + model_facts.json
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
