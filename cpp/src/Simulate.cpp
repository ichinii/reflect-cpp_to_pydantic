#include "toyscene/Simulate.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <numbers>
#include <random>
#include <type_traits>
#include <vector>

#include <glm/geometric.hpp>

#include "toyscene/Serialization.h"

namespace toyscene {
namespace {

/// Deterministic given the seed; `std::mt19937_64` is specified by the
/// standard, so the numbers do not drift between platforms or libstdc++
/// versions.
using Rng = std::mt19937_64;

/// A direction within `_halfAngle` of +z, sampled uniformly on the cap.
glm::dvec3 sampleCone(Rng& _rng, double _halfAngle) {
  std::uniform_real_distribution<double> unit(0.0, 1.0);
  const double cosMax = std::cos(_halfAngle);
  const double cosTheta = 1.0 - unit(_rng) * (1.0 - cosMax);
  const double sinTheta = std::sqrt(std::max(0.0, 1.0 - cosTheta * cosTheta));
  const double phi = 2.0 * std::numbers::pi * unit(_rng);
  return glm::dvec3(sinTheta * std::cos(phi), sinTheta * std::sin(phi), cosTheta);
}

/// Index into `_cumulative` (a cumulative weight table) by binary search.
size_t sampleWeighted(Rng& _rng, const std::vector<double>& _cumulative) {
  std::uniform_real_distribution<double> unit(0.0, _cumulative.back());
  const double target = unit(_rng);
  const auto it = std::lower_bound(_cumulative.begin(), _cumulative.end(), target);
  const auto index = static_cast<size_t>(it - _cumulative.begin());
  return index < _cumulative.size() ? index : _cumulative.size() - 1;
}

}  // namespace

std::vector<double> simulate(const Scene& _scene) {
  const auto rayCount = static_cast<size_t>(_scene.numRays.value());

  // Deterministic for a given seed; a scene without a seed still has to
  // produce something, so it falls back to a fixed value.
  Rng rng(_scene.seed.value_or(0x5EEDull));

  const double energyEv = _scene.source.visit(
      [](const auto& _source) { return electronVolts(_source.energy); });

  // The elements do nothing physical, but the output has to react to them so
  // that tests can tell two scenes apart: their positions and normals are
  // folded into a single offset applied to every ray origin.
  glm::dvec3 elementOffset(0.0, 0.0, 0.0);
  for (const auto& element : _scene.elements) {
    elementOffset += element.position * glm::dot(element.normal, glm::dvec3(0, 0, 1));
  }
  if (!_scene.elements.empty()) {
    elementOffset /= static_cast<double>(_scene.elements.size());
  }

  // Precompute whatever the source needs, once.
  const double divergence = _scene.source.visit([](const auto& _source) -> double {
    using S = std::remove_cvref_t<decltype(_source)>;
    if constexpr (std::is_same_v<S, PointSource>) {
      return radians(_source.divergence);
    } else {
      return 0.0;
    }
  });

  std::vector<double> cumulative;
  if (const auto* sampled = rfl::get_if<SampledSource>(&_scene.source.variant());
      sampled != nullptr && sampled->weights.has_value()) {
    const auto weights = sampled->weights->values();
    cumulative.reserve(weights.size());
    double total = 0.0;
    for (const double w : weights) {
      total += w;
      cumulative.push_back(total);
    }
  }

  std::vector<double> out(rayCount * kResultColumns);
  std::uniform_real_distribution<double> centered(-0.5, 0.5);

  for (size_t i = 0; i < rayCount; ++i) {
    glm::dvec3 origin(0.0, 0.0, 0.0);
    glm::dvec3 direction(0.0, 0.0, 1.0);

    _scene.source.visit([&](const auto& _source) {
      using S = std::remove_cvref_t<decltype(_source)>;
      if constexpr (std::is_same_v<S, PointSource>) {
        direction = sampleCone(rng, divergence);
      } else if constexpr (std::is_same_v<S, RectSource>) {
        origin = glm::dvec3(centered(rng) * _source.width.value(),
                            centered(rng) * _source.height.value(), 0.0);
      } else {
        const auto positions = _source.positions.values();
        const auto rows = _source.positions.shape()[0];
        const size_t row =
            cumulative.empty()
                ? std::uniform_int_distribution<size_t>(0, rows - 1)(rng)
                : sampleWeighted(rng, cumulative);
        origin = glm::dvec3(positions[row * 3], positions[row * 3 + 1],
                            positions[row * 3 + 2]);
      }
    });

    origin += elementOffset;

    double* row = out.data() + i * kResultColumns;
    row[0] = origin.x;
    row[1] = origin.y;
    row[2] = origin.z;
    row[3] = direction.x;
    row[4] = direction.y;
    row[5] = direction.z;
    row[6] = energyEv;
  }

  return out;
}

}  // namespace toyscene
