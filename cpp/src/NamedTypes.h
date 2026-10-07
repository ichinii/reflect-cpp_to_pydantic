#pragma once

/// Private: the registry of tagged-union aliases that should exist as *named*
/// types downstream.
///
/// reflect-cpp only emits named schema definitions for structs. A
/// `rfl::TaggedUnion` has no definition of its own: it is inlined as an `anyOf`
/// at every use site, so `Angle`, `PhotonEnergy`, `Source`, `Area` and
/// `Behavior` would not exist as types in the generated Python at all.
///
/// `rfl::json::to_schema<T>()` *does* work on a union directly, and returns the
/// `anyOf` as the document root with the member definitions carried along in
/// `$defs`. Exporting those lets `scripts/postprocess_schema.py` recognise the
/// inline occurrences and replace them with a reference to a named definition.
///
/// **Registering a union is one line in `namedUnions()` below.** The schema is
/// derived from the C++ type, so the C++ stays the single source of truth; the
/// only thing written by hand is the name it should carry in Python. If a
/// registered union no longer occurs in the schema, or if two registrations
/// become structurally identical, the post-processor fails the build.

#include <string>
#include <utility>
#include <vector>

#include <rfl.hpp>
#include <rfl/json.hpp>

#include "Reflectors.h"  // Source contains an Array<double>
#include "toyscene/Scene.h"

namespace toyscene::detail {

/// A union alias and the JSON Schema reflect-cpp produces for it.
struct NamedUnion {
  std::string name;
  std::string schema;
};

template <class T>
NamedUnion namedUnion(std::string _name) {
  return NamedUnion{.name = std::move(_name),
                    .schema = rfl::json::to_schema<T>()};
}

/// Every `rfl::TaggedUnion` alias in Scene.h that should be a named type.
inline std::vector<NamedUnion> namedUnions() {
  return {
      namedUnion<Angle>("Angle"),
      namedUnion<PhotonEnergy>("PhotonEnergy"),
      namedUnion<Source>("Source"),
      namedUnion<Area>("Area"),
      namedUnion<Behavior>("Behavior"),
  };
}

}  // namespace toyscene::detail
