#pragma once

/// The scene description. This is the single definition of the data model:
/// the JSON Schema and, from it, the Pydantic models are derived from these
/// declarations, never the other way round.
///
/// reflect-cpp's own vocabulary types are used directly -- `rfl::Validator` for
/// value guards, `rfl::TaggedUnion` for variants -- so that every constraint
/// reaches the exported schema. `rfl::*` and `glm::*` are public dependencies
/// of this library.

#include <cstdint>
#include <optional>
#include <vector>

#include <glm/vec3.hpp>
#include <rfl.hpp>

#include "toyscene/Array.h"
#include "toyscene/Types.h"

namespace toyscene {

// --- angles ----------------------------------------------------------------

struct Deg {
  FiniteDouble value;
};

struct Rad {
  FiniteDouble value;
};

using Angle = rfl::TaggedUnion<"type", Deg, Rad>;

/// The angle in radians, whichever way it was given.
double radians(const Angle& _angle);

// --- photon energy ---------------------------------------------------------

struct Wavelength {
  PositiveDouble nm;
};

struct ElectronVolt {
  PositiveDouble eV;
};

using PhotonEnergy = rfl::TaggedUnion<"type", Wavelength, ElectronVolt>;

/// The energy in electron volts, whichever way it was given.
double electronVolts(const PhotonEnergy& _energy);

// --- sources ---------------------------------------------------------------

struct PointSource {
  Angle divergence = Rad{0.0};
  PhotonEnergy energy;
};

struct RectSource {
  NonNegativeDouble width;
  NonNegativeDouble height;
  PhotonEnergy energy;
};

struct SampledSource {
  Array<double> positions;                // shape (N, 3)
  std::optional<Array<double>> weights;   // shape (N,)
  PhotonEnergy energy;

  /// Cross-field and non-schema rules: shapes agree, weights are finite and
  /// non-negative, positions are finite. Throws SceneError.
  void validate() const;
};

using Source = rfl::TaggedUnion<"type", PointSource, RectSource, SampledSource>;

// --- element geometry ------------------------------------------------------

struct RectArea {
  PositiveDouble width;
  PositiveDouble height;
};

struct EllipseArea {
  PositiveDouble a;
  PositiveDouble b;
};

using Area = rfl::TaggedUnion<"type", RectArea, EllipseArea>;

// --- element behavior ------------------------------------------------------

struct Mirror {
  UnitIntervalDouble reflectivity = 1.0;
};

struct Detector {};

struct Grating {
  PositiveDouble lineDensity;
  int order = 1;
};

using Behavior = rfl::TaggedUnion<"type", Mirror, Detector, Grating>;

// --- elements and scene ----------------------------------------------------

struct Element {
  NonEmptyString name;
  glm::dvec3 position = {0, 0, 0};
  glm::dvec3 normal = {0, 0, 1};
  Area area;
  Behavior behavior;

  /// Non-schema rules: position and normal are finite and the normal is a unit
  /// vector. Throws SceneError.
  void validate() const;
};

struct Scene {
  NonEmptyString name;
  RayCount numRays = 10'000;
  std::optional<uint64_t> seed;
  Source source;
  std::vector<Element> elements;
};

/// Tolerance used when checking that `Element::normal` has unit length.
inline constexpr double kUnitNormalTolerance = 1e-9;

}  // namespace toyscene
