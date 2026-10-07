# toyscene — notes for Claude Code sessions

`README.md` orients a newcomer: what this is, how to build it, and a worked
`Mirror` walkthrough from C++ through JSON Schema to Pydantic. This file is the
reference material behind it — the reflect-cpp limitations that shape the
pipeline, and the traps that look like bugs but are load-bearing decisions.

## Working rules

* **Everything runs inside `nix develop`.** Use `python -m pytest`, not `pytest`:
  the `pytest` on `PATH` comes from the nix environment and does not see the
  `.venv` that the devShell creates.
* **`python/toyscene/_models.py` and `schema/*.json` are generated and
  committed.** Never hand-edit them. The data model lives in
  `cpp/include/toyscene/Scene.h` and `cpp/include/toyscene/Types.h`; change the
  C++ and run `./scripts/generate_models.sh`.
* **`tests/test_schema_drift.py` is the guard.** It runs the whole pipeline into
  a temporary directory and fails if any committed file is stale. Its fifth check
  compares the *compiled* extension against the committed schema, so a stale
  `build-cpp` cannot make the other tests lie. If it fails after a `Scene.h`
  change, the fix is to regenerate, not to edit the expectation.
* A new `rfl::Validator` alias, a new struct, or a new field needs nothing but
  the C++ edit plus a regeneration. A new *named union* additionally needs a line
  in `cpp/src/NamedTypes.h` and a re-export in `python/toyscene/__init__.py`.

## Who owns what in the pipeline

* `cpp/tools/export_schema.cpp` writes all three exported files:
  `scene.schema.json` (reflect-cpp's output verbatim), `model_facts.json` (what
  that schema cannot say), `named_unions.json` (one `to_schema<T>()` per
  registered union).
* `scripts/postprocess_schema.py` turns those into
  `scene.pydantic.schema.json`: cleans definition names, merges nested `allOf`,
  injects defaults, hoists and names unions, adds discriminators.
* `scripts/postprocess_models.py` fixes what the generator cannot be configured
  into doing: rewrites each `RootModel` wrapper into a type alias and swaps in the
  numpy-backed array types.
* `scripts/generate_models.sh` carries a flag-by-flag comment on every
  `datamodel-codegen` option. Read it there rather than duplicating it here.

## Required vs. defaulted fields, and why `model_facts.json` exists

**Outcome: C++ requires every non-`std::optional` field to be present; the
defaults reach Python through the schema, and Pydantic always writes a complete
document.**

reflect-cpp has no per-field notion of "optional with a default":

* `rfl::parsing::schema::Type` — the variant that `to_schema` walks — has no
  default node at all. `Optional`, `Description`, `Deprecated`, `Literal`,
  `Object` … a default value is simply not representable, so there is nothing for
  the JSON Schema writer to emit.
* `rfl::json::to_schema` therefore puts every non-`std::optional` field in
  `required`.
* The `rfl::DefaultIfMissing` processor is a parser marker (its `process` is the
  identity). Its only schema effect is `to_schema`'s single global `_no_required`
  flag, which drops `required` entirely — all-or-nothing, which would make
  genuinely required fields optional in the generated models.
* `rfl::DefaultVal<T>`'s `to_schema` passes straight through to the wrapped type,
  so it does not affect the schema either.

Worse, neither is usable for *parsing* this model. Both route the parser through
`read_struct_with_default`, which starts from `T{}` — and
`rfl::Validator<double, rfl::ExclusiveMinimum<0>>{}` throws, because it validates
`double{}` and `0 > 0` is false. That throw would happen inside a function marked
`noexcept`, i.e. `std::terminate`. So `fromJson` uses neither, and a missing field
is an error. See the comment on `fromJson` in `cpp/src/Serialization.cpp`.

The bridge: `export_schema.cpp` writes `schema/model_facts.json`, and
`postprocess_schema.py` uses it to drop each defaulted field from `required` and
add a `default`. The default *values* are not restated there — `modelFacts()`
reads them off real C++ objects (`Mirror{}.reflectivity`,
`Grating{.lineDensity = 1.0}.order`, …), so a changed default in `Scene.h` changes
the schema and fails the drift test. Only the struct and field *names* are written
by hand, and the post-processor refuses a name that is not a currently-required
property of that definition.

`model_facts.json` also carries `bufferRefDefinition` (reflect-cpp's own `$defs`
name for `Array<double>`, taken from `rfl::parsing::make_type_name`, so a library
rename cannot silently desynchronise the post-processor) and `arrays` — the
`dtype` and `ndim` of each buffer-backed field. **`ndim` is the one genuinely
hand-written fact in the file**: `Array<T>` carries a runtime `shape`, so its type
says nothing about the expected rank. The expectation is also enforced in
`SampledSource::validate`, and the post-processor fails if the field it names is
no longer a buffer reference.

## NaN and the `Finite` rule

**Outcome: reflect-cpp's built-in numeric rules do not reject NaN, so there is a
custom `Finite` rule, and it is part of every floating-point alias.**

The built-in rules are written as "fail if out of range":
`rfl::Minimum<0>::validate` errors when `value < threshold`. Every comparison
involving NaN is false, so NaN passes `Minimum`, `Maximum`, `ExclusiveMinimum`, …
and every `AllOf` of them. `toyscene::Finite` in `Types.h` closes that
(`std::isfinite`), in reflect-cpp's validator format, so it composes and reaches
the schema.

Pydantic reads the same bounds the other way round — "require in range", which NaN
fails — so a bounded field rejects NaN there for free. That asymmetry is what makes
the schema translation of `Finite` work at all: `parsing::schema::ValidationType`
is a **closed** variant, so a custom rule can only express itself in terms of rules
reflect-cpp already knows. `Finite` emits
`AllOf<Minimum<-DBL_MAX>, Maximum<DBL_MAX>>`, which under IEEE comparison *is*
finiteness, and which makes the generated models reject NaN and ±∞ even for a field
whose only guard is finiteness (`Deg.value`, `Rad.value`). That is where the
`Field(ge=-1.7976931348623157e308, …)` in `_models.py` comes from. The schema
post-processor keeps only the tightest bound per keyword, so these limits disappear
wherever a real bound already exists (`Mirror.reflectivity` comes out as plain
`ge=0, le=1`).

Two things worth knowing:

* A non-finite **scalar** cannot reach a guard through JSON at all. reflect-cpp's
  reader rejects the non-standard `NaN`/`Infinity` literals as syntax errors and
  refuses an exponent that overflows a double (`1e999` → "number is infinity when
  parsed as double"). The `Finite` rule earns its keep on scenes built in C++, on
  assignment, in the schema, and on bulk buffers.
* A numpy **buffer** is the path NaN actually takes into a scene, since it never
  passes through a JSON number. Those are checked element-wise in
  `SampledSource::validate`.

## Traps — do not "fix" these

* **The exported schema has no `additionalProperties: false`, and adding it would
  break things.** `fromJson` uses `rfl::NoExtraFields`, so C++ rejects unknown
  fields, and the generated models forbid them via `extra="forbid"` in
  `python/toyscene/_base.py`. But `rfl::NoExtraFields` is a *reader-side* processor
  that `rfl::json::to_schema` does not reflect. Adding the keyword in
  post-processing is not worth it: datamodel-code-generator then writes
  `model_config = ConfigDict(extra='forbid')` into every class, which *replaces*
  the base class's config and silently drops `validate_default=True` — on which the
  defaulted tagged-union field depends.
* **`--collapse-root-models` is deliberately absent** from the `datamodel-codegen`
  invocation. It does the opposite of what is wanted: it inlines each hoisted union
  back at every use site, undoing the naming. `postprocess_models.py` rewrites the
  wrappers into aliases instead.

## Union hoisting internals

**reflect-cpp only emits named schema definitions for structs.** A
`rfl::TaggedUnion` gets none: it is inlined as an `anyOf` of `$ref`s at every use
site, so nothing in `scene.schema.json` says that the two-member union under
`PointSource.divergence` and the one under `Element.area` are different types, or
that the three-member one appears under `Scene.source`. Generated straight from
that schema, the unions simply would not exist.

`rfl::json::to_schema<T>()` *does* work on a union directly, and returns the
`anyOf` as the document root with the member definitions carried along under
`$defs`. `cpp/src/NamedTypes.h` is the registry of aliases that should be named;
`export_schema` writes each one's schema to `named_unions.json`, and
`postprocess_schema.py` replaces every structurally identical inline occurrence
with a reference to a definition under that name.

Four details that could not be configured away:

* **Definition names.** reflect-cpp derives `$defs` keys from C++ type names with
  every non-alphanumeric character replaced by `_`, and appends `__tagged` to a
  struct used inside a tagged union, so `Mirror` arrives as
  `toyscene__Mirror__tagged`. Those names would become the Python class names, so
  they are stripped. A struct used both inside a union *and* on its own would
  produce `Foo` and `Foo__tagged`, which clean to the same name; the tagged form
  wins, because it carries the `type` literal the discriminated union needs. That
  is wrong for a plain field of such a type — it would start demanding a `type`
  property — so the post-processor raises if more than one form is referenced from
  outside a union, and warns if only the plain one is. Nothing in the current model
  triggers either.
* **Aliases rather than `RootModel`.** datamodel-code-generator 0.35.0 has no
  type-alias option; it emits a `RootModel` subclass for a definition whose body is
  an `anyOf`. `postprocess_models.py` rewrites each wrapper into the alias it
  should have been, walking the AST rather than matching text, and fails if a
  registered union did not come out as a `RootModel`. It also strips the empty
  `Field()` that the generator wraps a defaulted alias in
  (`Annotated[Angle, Field()]`), which otherwise stops the annotation from *being*
  `Angle`.
* **Discriminators.** `rfl::TaggedUnion` becomes a plain `anyOf` with no
  `discriminator`. The post-processor adds the OpenAPI-style keyword wherever every
  branch is a definition with a one-value `type` enum, which is what makes the
  generated models use `Field(discriminator='type')` and produce useful errors
  instead of trying every branch.
* **Validator composition.** A composed `rfl::Validator` arrives as a *nested*
  `allOf` of single-keyword objects. The post-processor merges those into one
  object, keeping the tightest bound per keyword — which is exactly `allOf`
  semantics for `minimum`/`maximum`/`minLength`/… and nothing more.

## When the union registry goes stale

Post-processing fails the build rather than silently producing a different shape:

| situation | error |
| --- | --- |
| a registered union no longer occurs in the schema (the C++ alias is unused, or its member list changed) | `registered union(s) ['Angle'] do not occur in the schema` |
| two registrations have identical members, so which name an inline block becomes is arbitrary | `an inline union matches several registered names [...]` |
| a registered name is already a struct name | `cannot hoist union 'X': the schema already defines that name` |
| the two exported files were edited apart | `union 'X' and the main schema disagree about 'Y'` |
| the generator stopped wrapping an `anyOf` definition in a `RootModel`, so there is nothing to alias | `the generator did not emit a root model for [...]` |

Matching is structural and order-sensitive, on the member list as reflect-cpp
writes it. Reordering `rfl::TaggedUnion<"type", Rad, Deg>` is therefore fine: both
files move together. Adding a member to a union that is *not* registered is also
fine — it stays inline.

## `rfl::Validator` semantics

Verified and tested in `cpp/tests/test_scene.cpp`:

* **It is default-constructible**, so leaving a guarded field out of an aggregate
  initializer compiles. `Validator()` validates `T()` and throws if that fails:
  `PositiveDouble()` throws, while `UnitIntervalDouble()` quietly yields `0.0`
  because 0 is inside `[0, 1]`. A missing required field is therefore a run-time
  surprise rather than a compile error — which is also why `read_struct_with_default`
  is unusable here.
* **Assignment re-validates.** `operator=` runs the rules, throws on failure, and
  leaves the previous value in place.

## C++ internals worth knowing before editing

* **`thread_local` buffer tables.** `fromJson`/`toJson` are safe to call from
  several threads, but an `Array<T>` cannot be parsed outside one of the scopes.
  The scopes save and restore the previous table, so nesting is safe too.
* **`Array<T>` from `fromJson` is a view** into the caller's buffers. The nanobind
  layer relies on the argument tuple keeping the numpy arrays alive for the
  duration of the call, which is all `simulate` needs; a `Scene` that outlives the
  call would need `BufferView::owner` set.
* **`toJson` renumbers buffer references** in traversal order, so a round trip
  preserves the scene but not the original buffer indices. Its signature returns
  only a string, so the written buffers are not handed back — the write-side table
  only hands out indices.
* **Only `float64` exists.** `dtypeName<T>()` is specialized for `double` alone,
  so another element type is a one-line addition plus a `model_facts` entry.
* **`glm::dvec3` needs no post-processing for correctness.**
  `rfl::Reflector<glm::dvec3>` declares `ReflType = std::array<double, 3>`, and
  reflect-cpp maps fixed-size arrays to `FixedSizeTypedArray`, which the JSON
  Schema writer emits with `minItems`/`maxItems` — so the length *is* pinned, and
  both sides reject a vector of 2 or 4 elements. The only step taken is cosmetic:
  inlining it at its two use sites, so the field becomes
  `Annotated[list[float], Field(min_length=3, max_length=3)]` rather than a wrapper
  model named after `glm__vec_3__double__glm__packed_highp_`.

## Deviation from the specified model

`Deg` and `Rad` hold a `FiniteDouble` rather than a bare `double`. The
specification says `double value;`, but then also says the finiteness rule should
be in every floating-point alias — and an unguarded `double` here is the one hole
through which a NaN angle could reach the simulation from a C++ caller. It costs
one alias and makes the generated models reject a NaN angle too.

## Scope

The simulation is a placeholder. It samples origins and directions from the source,
and folds element positions and normals into a single offset so that the output
reacts to the scene; the elements do nothing physical. Do not read physics into
`cpp/src/Simulate.cpp`.
