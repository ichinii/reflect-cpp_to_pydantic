# toyscene

A prototype of one architecture: **the scene description is defined once, in C++**,
and everything else is derived from it.

```
cpp/include/toyscene/Scene.h          plain aggregate structs, rfl::Validator guards,
       |                              rfl::TaggedUnion variants, glm::dvec3
       |  reflect-cpp
       +--> JSON parsing + validation           (cpp/src/Serialization.cpp)
       +--> schema/scene.schema.json            (cpp/tools/export_schema.cpp)
       +--> schema/named_unions.json            (cpp/src/NamedTypes.h registry)
                |  scripts/generate_models.sh
                +--> python/toyscene/_models.py  Pydantic v2 models, generated
                                                 and committed, never hand-written
```

Python users build a scene out of the generated models and call `simulate(scene)`.
The scene crosses the boundary as JSON; bulk arrays do not travel inside that
JSON but alongside it as numpy buffers, so nothing large is copied in either
direction. The result comes back as a `(numRays, 7)` float64 array that Python
owns without a copy.

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

Everything happens inside the nix devShell, which pins the toolchain
(nixpkgs `nixos-25.11`: gcc 14.3, cmake 4.1, ninja, glm 1.0.3, Python 3.12 with
numpy 2.3.4, pydantic 2.11.7, datamodel-code-generator 0.35.0, nanobind 2.9.2,
scikit-build-core 0.11.5, pytest 8.4.2).

```sh
nix develop                              # creates and activates .venv on entry
pip install --no-build-isolation -e .     # builds toyscene._core via scikit-build-core
python -m pytest -q                       # Python tests
cmake -S . -B build-cpp && cmake --build build-cpp && ctest --test-dir build-cpp
python examples/basic.py
```

`nix develop` creates a `.venv` with `--system-site-packages` and activates it,
because nixpkgs' `site-packages` is read-only and an editable install needs
somewhere to write. Use `python -m pytest` rather than `pytest`: the `pytest`
on `PATH` comes from the nix environment and does not see the venv.

reflect-cpp **is not packaged in nixpkgs**, so CMake fetches it with
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

so they can be used in annotations of your own, and a variant value *is* the
variant -- `isinstance(source.divergence, ts.Deg)`, no `.root` to unpack.

### Why this needs post-processing

**reflect-cpp only emits named schema definitions for structs.** A
`rfl::TaggedUnion` gets none: it is inlined as an `anyOf` of `$ref`s at every
use site, so nothing in `schema/scene.schema.json` says that the two-member
union under `PointSource.divergence` and the one under `Element.area` are
different types, or that the three-member one appears under `Scene.source`.
Generated straight from that schema, the unions simply would not exist.

`rfl::json::to_schema<T>()` *does* work on a union directly, and returns the
`anyOf` as the document root with the member definitions carried along under
`$defs`. `cpp/src/NamedTypes.h` is a registry of the aliases that should be
named; `export_schema` writes each one's schema to `schema/named_unions.json`,
and `scripts/postprocess_schema.py` replaces every structurally identical inline
occurrence with a reference to a definition under that name. The schemas come
from the C++ types, so the C++ remains the single source of truth -- the only
thing written by hand is the Python-facing name.

### Registering a new union

One line in `namedUnions()` in `cpp/src/NamedTypes.h`:

```cpp
namedUnion<Polarization>("Polarization"),
```

then `./scripts/generate_models.sh`, and add the name to the re-export list in
`python/toyscene/__init__.py`.

### What breaks when the registry is stale

The post-processing fails the build rather than silently producing a different
shape:

| situation | error |
| --- | --- |
| a registered union no longer occurs in the schema (the C++ alias is unused, or its member list changed) | `registered union(s) ['Angle'] do not occur in the schema` |
| two registrations have identical members, so which name an inline block becomes is arbitrary | `an inline union matches several registered names [...]` |
| a registered name is already a struct name | `cannot hoist union 'X': the schema already defines that name` |
| the two exported files were edited apart | `union 'X' and the main schema disagree about 'Y'` |
| the generator stopped wrapping an `anyOf` definition in a `RootModel`, so there is nothing to alias | `the generator did not emit a root model for [...]` |

Matching is structural and order-sensitive, on the member list as reflect-cpp
writes it. Reordering `rfl::TaggedUnion<"type", Rad, Deg>` is therefore fine:
both files move together. Adding a member to a union that is *not* registered is
also fine -- it stays inline, as it is today.

### Two things that could not be configured away

* **Definition names.** reflect-cpp derives `$defs` keys from C++ type names
  with every non-alphanumeric character replaced by `_`, and appends `__tagged`
  to a struct used inside a tagged union, so `Mirror` arrives as
  `toyscene__Mirror__tagged`. Those names would become the Python class names,
  so they are stripped. A struct used both inside a union *and* on its own would
  produce `Foo` and `Foo__tagged`, which clean to the same name; the tagged form
  wins, because it carries the `type` literal the discriminated union needs.
  That is wrong for a plain field of such a type -- it would start demanding a
  `type` property -- so the post-processor raises if more than one form is
  referenced from outside a union, and warns if only the plain one is. Nothing
  in the current model triggers either.

* **Aliases rather than `RootModel`.** datamodel-code-generator 0.35.0 has no
  type-alias option; it emits a `RootModel` subclass for a definition whose body
  is an `anyOf`. `--collapse-root-models` does the opposite of what is wanted --
  it inlines the union back at every use site, undoing the hoist -- so it is not
  used, and `scripts/postprocess_models.py` rewrites each wrapper into the alias
  it should have been. The rewrite walks the AST rather than matching text, and
  fails if a registered union did not come out as a `RootModel`. It also strips
  the empty `Field()` that the generator wraps a defaulted alias in
  (`Annotated[Angle, Field()]`), which otherwise stops the annotation from
  *being* `Angle`.

## Decisions and limitations

### Required vs. defaulted fields

**Outcome: C++ requires every non-`std::optional` field to be present; the
defaults reach Python through the schema, and Pydantic always writes a complete
document.**

reflect-cpp has no per-field notion of "optional with a default":

* `rfl::json::to_schema` puts every non-`std::optional` field in `required`.
* The `rfl::DefaultIfMissing` processor flips a *single global* flag
  (`to_schema`'s `_no_required`) that drops `required` entirely — all-or-nothing,
  which would make genuinely required fields optional in the generated models.
* `rfl::DefaultVal<T>`'s `to_schema` passes straight through to the wrapped
  type, so it does not affect the schema either.

Worse, neither is usable for *parsing* this model. Both route the parser through
`read_struct_with_default`, which starts from `T{}` — and
`rfl::Validator<double, rfl::ExclusiveMinimum<0>>{}` throws, because it validates
`double{}` and `0 > 0` is false. That throw would happen inside a function
marked `noexcept`, i.e. `std::terminate`. So `fromJson` uses neither, and a
missing field is an error.

The smallest step that bridges the gap: `cpp/tools/export_schema.cpp` writes
`schema/model_facts.json`, and `scripts/postprocess_schema.py` uses it to drop
each defaulted field from `required` and add a `default`. The default *values*
are not restated there — the exporter reads them off real C++ objects
(`Mirror{}.reflectivity`, `Grating{.lineDensity = 1.0}.order`, …), so a changed
default in `Scene.h` changes the schema and fails the drift test. Only the
struct and field *names* are written by hand, and the post-processor refuses a
name that is not a currently-required property of that definition.

The practical consequence: a field with a C++ default is optional **in Python**,
not in the JSON. Pydantic fills defaults in before serializing, so complete
documents are what C++ ever sees. A hand-written document passed straight to
`_core.simulate_json` must spell out every non-optional field.

### NaN

**Outcome: reflect-cpp's built-in numeric rules do not reject NaN, so there is a
custom `Finite` rule, and it is part of every floating-point alias.**

The built-in rules are written as "fail if out of range":
`rfl::Minimum<0>::validate` errors when `value < threshold`. Every comparison
involving NaN is false, so NaN passes `Minimum`, `Maximum`,
`ExclusiveMinimum`, … and every `AllOf` of them. `toyscene::Finite` in `Types.h`
closes that (`std::isfinite`), in reflect-cpp's validator format, so it composes
and reaches the schema.

Pydantic reads the same bounds the other way round — "require in range", which
NaN fails — so a bounded field rejects NaN there for free. That asymmetry is
what makes the schema translation of `Finite` work at all:
`parsing::schema::ValidationType` is a **closed** variant, so a custom rule can
only express itself in terms of rules reflect-cpp already knows. `Finite` emits
`AllOf<Minimum<-DBL_MAX>, Maximum<DBL_MAX>>`, which under IEEE comparison *is*
finiteness, and which makes the generated models reject NaN and ±∞ even for a
field whose only guard is finiteness (`Deg.value`, `Rad.value`). The cost is
visible: `Field(ge=-1.7976931348623157e308, …)` in `_models.py`. The schema
post-processor keeps only the tightest bound per keyword, so these limits
disappear wherever a real bound already exists (`Mirror.reflectivity` comes out
as plain `ge=0, le=1`).

Two things worth knowing:

* A non-finite **scalar** cannot reach a guard through JSON at all. reflect-cpp's
  reader rejects the non-standard `NaN`/`Infinity` literals as syntax errors and
  refuses an exponent that overflows a double (`1e999` → "number is infinity when
  parsed as double"). The `Finite` rule earns its keep on scenes built in C++, on
  assignment, in the schema, and on bulk buffers.
* A numpy **buffer** is the path NaN actually takes into a scene, since it never
  passes through a JSON number. Those are checked element-wise in
  `SampledSource::validate`.

### `rfl::Validator`

Verified and tested in `cpp/tests/test_scene.cpp`:

* **It is default-constructible**, so leaving a guarded field out of an aggregate
  initializer compiles. `Validator()` validates `T()` and throws if that fails:
  `PositiveDouble()` throws, while `UnitIntervalDouble()` quietly yields `0.0`
  because 0 is inside `[0, 1]`. A missing required field is therefore a run-time
  surprise rather than a compile error — see the required-fields section for why
  this matters.
* **Assignment re-validates.** `operator=` runs the rules, throws on failure, and
  leaves the previous value in place.

### The `glm::dvec3` schema

`rfl::Reflector<glm::dvec3>` declares `ReflType = std::array<double, 3>` and the
schema falls out of that automatically — **and it does pin the length**:

```json
{"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
```

(reflect-cpp maps fixed-size arrays to its internal `FixedSizeTypedArray`, which
the JSON Schema writer emits with `minItems`/`maxItems`, not `prefixItems`.) No
post-processing was needed for correctness. The one cosmetic step: reflect-cpp
puts it in `$defs` under `glm__vec_3__double__glm__packed_highp_`, and inlining
it at its two use sites turns the generated field into
`Annotated[list[float], Field(min_length=3, max_length=3)]` instead of a wrapper
model. Both sides reject a vector of 2 or 4 elements.

### Other things the schema cannot express

* **The rank of an `Array<T>` field.** `Array<T>` carries a runtime `shape`, so
  its type says nothing about the expected number of dimensions. This is the one
  genuinely hand-written fact in `schema/model_facts.json` (`positions` is 2-d,
  `weights` is 1-d); the expectation is also enforced in
  `SampledSource::validate`, and the post-processor fails if the field it names
  is no longer a buffer reference.
* **Unknown fields.** `fromJson` uses `rfl::NoExtraFields`, so C++ rejects them,
  and the generated models forbid them via `extra="forbid"` in
  `python/toyscene/_base.py`. But `rfl::NoExtraFields` is a *reader-side*
  processor that `rfl::json::to_schema` does not reflect, so the exported schema
  carries no `additionalProperties: false`. Adding it in post-processing is not
  worth it: datamodel-code-generator then writes
  `model_config = ConfigDict(extra='forbid')` into every class, which *replaces*
  the base class's config and silently drops `validate_default=True` — on which
  the defaulted tagged-union field depends.
* **Discriminated unions.** `rfl::TaggedUnion` becomes a plain `anyOf` with no
  `discriminator` and no name. The post-processor adds the OpenAPI-style keyword
  wherever every branch is a definition with a one-value `type` enum, which is
  what makes the generated models use `Field(discriminator='type')` and produce
  useful errors instead of trying every branch. Naming the unions is a separate
  step; see above.
* **Validator composition.** A composed `rfl::Validator` arrives as a *nested*
  `allOf` of single-keyword objects. The post-processor merges those into one
  object, keeping the tightest bound per keyword — which is exactly `allOf`
  semantics for `minimum`/`maximum`/`minLength`/… and nothing more.

### Deviation from the specified model

`Deg` and `Rad` hold a `FiniteDouble` rather than a bare `double`. The
specification says `double value;`, but then also says the finiteness rule should
be in every floating-point alias — and an unguarded `double` here is the one hole
through which a NaN angle could reach the simulation from a C++ caller. It costs
one alias and makes the generated models reject a NaN angle too.

### Scope

* The simulation is a placeholder. It samples origins and directions from the
  source, and folds element positions and normals into a single offset so that
  the output reacts to the scene; the elements do nothing physical.
* `toJson` renumbers buffer references in traversal order, so a round trip
  preserves the scene but not necessarily the original buffer indices. Its
  signature returns only a string, so the written buffers are not handed back —
  the write-side table only hands out indices.
* `thread_local` buffer tables mean `fromJson`/`toJson` are safe to call from
  several threads but an `Array<T>` cannot be parsed outside one of them. The
  scopes save and restore the previous table, so nesting is safe too.
* `Array<T>` instances produced by `fromJson` are *views* into the caller's
  buffers. The nanobind layer relies on the argument tuple keeping the numpy
  arrays alive for the duration of the call, which is all `simulate` needs; a
  `Scene` that outlives the call would need `BufferView::owner` set.
* Only `float64` arrays exist. `dtypeName<T>()` is specialized for `double`
  alone, so another element type is a one-line addition plus a `model_facts`
  entry.

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
