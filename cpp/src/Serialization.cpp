#include "toyscene/Serialization.h"

#include <rfl/json.hpp>

// Must precede any rfl::json::read/write instantiation for Scene so that the
// Reflector specializations are visible.
#include "Reflectors.h"

#include <cmath>
#include <numbers>
#include <string>
#include <type_traits>
#include <utility>

#include <glm/geometric.hpp>

namespace toyscene {
namespace {

/// Planck constant times the speed of light, in eV*nm.
constexpr double kHcEvNm = 1239.8419843320026;

// The buffer table for the innermost fromJson/toJson call on this thread.
thread_local std::span<const BufferView> tlsReadBuffers{};
thread_local bool tlsReadActive = false;
thread_local size_t tlsWriteNext = 0;
thread_local bool tlsWriteActive = false;

std::string shapeToString(const std::vector<size_t>& _shape) {
  std::string out = "(";
  for (size_t i = 0; i < _shape.size(); ++i) {
    out += (i ? ", " : "") + std::to_string(_shape[i]);
  }
  return out + ")";
}

void requireFinite(const glm::dvec3& _v, const char* _field) {
  if (!std::isfinite(_v.x) || !std::isfinite(_v.y) || !std::isfinite(_v.z)) {
    throw SceneError(std::string(_field) +
                     ": every component must be finite, but got [" +
                     std::to_string(_v.x) + ", " + std::to_string(_v.y) + ", " +
                     std::to_string(_v.z) + "]");
  }
}

}  // namespace

namespace detail {

ReadBufferScope::ReadBufferScope(std::span<const BufferView> _buffers)
    : previous_(tlsReadBuffers), previouslyActive_(tlsReadActive) {
  tlsReadBuffers = _buffers;
  tlsReadActive = true;
}

ReadBufferScope::~ReadBufferScope() {
  tlsReadBuffers = previous_;
  tlsReadActive = previouslyActive_;
}

WriteBufferScope::WriteBufferScope()
    : previous_(tlsWriteNext), previouslyActive_(tlsWriteActive) {
  tlsWriteNext = 0;
  tlsWriteActive = true;
}

WriteBufferScope::~WriteBufferScope() {
  tlsWriteNext = previous_;
  tlsWriteActive = previouslyActive_;
}

const BufferView& readBuffer(size_t _index) {
  if (!tlsReadActive) {
    throw SceneError(
        "refers to buffer " + std::to_string(_index) +
        ", but no buffers were handed over (arrays can only be read through "
        "toyscene::fromJson)");
  }
  if (_index >= tlsReadBuffers.size()) {
    throw SceneError("refers to buffer " + std::to_string(_index) + ", but only " +
                     std::to_string(tlsReadBuffers.size()) +
                     " buffer(s) were handed over");
  }
  return tlsReadBuffers[_index];
}

size_t nextWriteIndex() {
  if (!tlsWriteActive) {
    throw SceneError(
        "arrays can only be written through toyscene::toJson, which assigns "
        "the buffer indices");
  }
  return tlsWriteNext++;
}

}  // namespace detail

// --- unit conversions ------------------------------------------------------

double radians(const Angle& _angle) {
  return _angle.visit([](const auto& _v) -> double {
    using V = std::remove_cvref_t<decltype(_v)>;
    if constexpr (std::is_same_v<V, Deg>) {
      return _v.value.value() * std::numbers::pi / 180.0;
    } else {
      return _v.value.value();
    }
  });
}

double electronVolts(const PhotonEnergy& _energy) {
  return _energy.visit([](const auto& _v) -> double {
    using V = std::remove_cvref_t<decltype(_v)>;
    if constexpr (std::is_same_v<V, Wavelength>) {
      return kHcEvNm / _v.nm.value();
    } else {
      return _v.eV.value();
    }
  });
}

// --- cross-field and non-schema rules --------------------------------------

void SampledSource::validate() const {
  if (positions.ndim() != 2 || positions.shape()[1] != 3) {
    throw SceneError("positions: expected shape (N, 3), but got " +
                     shapeToString(positions.shape()));
  }
  const auto rows = positions.shape()[0];
  if (rows == 0) {
    throw SceneError("positions: must hold at least one row");
  }
  for (const double v : positions.values()) {
    if (!std::isfinite(v)) {
      throw SceneError("positions: every value must be finite");
    }
  }
  if (weights) {
    if (weights->ndim() != 1) {
      throw SceneError("weights: expected shape (N,), but got " +
                       shapeToString(weights->shape()));
    }
    if (weights->shape()[0] != rows) {
      throw SceneError("weights: has " + std::to_string(weights->shape()[0]) +
                       " entries, but positions has " + std::to_string(rows) +
                       " rows");
    }
    double total = 0.0;
    for (const double w : weights->values()) {
      if (!std::isfinite(w)) {
        throw SceneError("weights: every value must be finite");
      }
      if (w < 0.0) {
        throw SceneError("weights: every value must be >= 0, but got " +
                         std::to_string(w));
      }
      total += w;
    }
    if (total <= 0.0) {
      throw SceneError("weights: must not sum to zero");
    }
  }
}

void Element::validate() const {
  requireFinite(position, "position");
  requireFinite(normal, "normal");
  const double length = glm::length(normal);
  if (std::abs(length - 1.0) > kUnitNormalTolerance) {
    throw SceneError("normal: expected a unit vector, but |normal| = " +
                     std::to_string(length));
  }
}

void validate(const Scene& _scene) {
  _scene.source.visit([](const auto& _source) {
    if constexpr (requires { _source.validate(); }) {
      try {
        _source.validate();
      } catch (const SceneError& e) {
        throw SceneError(std::string("source.") + e.what());
      }
    }
  });

  for (size_t i = 0; i < _scene.elements.size(); ++i) {
    try {
      _scene.elements[i].validate();
    } catch (const SceneError& e) {
      throw SceneError("elements[" + std::to_string(i) + "]." + e.what());
    }
  }
}

// --- the JSON boundary -----------------------------------------------------

Scene fromJson(std::string_view _json, std::span<const BufferView> _buffers) {
  const detail::ReadBufferScope scope(_buffers);

  // No rfl::DefaultIfMissing here, and no rfl::DefaultVal on any field: both
  // route the parser through read_struct_with_default(), which starts from
  // `T{}`. Every struct in this model contains a guarded value whose default
  // fails its own rule (rfl::Validator<double, ExclusiveMinimum<0>>{} throws),
  // and it would throw from inside a noexcept function. Missing fields are
  // therefore an error here; the defaults reach Python through the schema
  // instead. See the README.
  //
  // rfl::NoExtraFields makes an unknown field an error instead of being
  // ignored, which is what the generated models do as well (extra="forbid" in
  // python/toyscene/_base.py). Note that it is a reader-side processor only:
  // rfl::json::to_schema does not reflect it, so the exported schema says
  // nothing about additional properties.
  auto result = rfl::json::read<Scene, rfl::NoExtraFields>(_json);
  if (!result.has_value()) {
    throw SceneError(result.error().what());
  }
  auto scene = std::move(result).value();
  validate(scene);
  return scene;
}

std::string toJson(const Scene& _scene) {
  const detail::WriteBufferScope scope;
  return rfl::json::write(_scene);
}

std::string jsonSchema() {
  return rfl::json::to_schema<Scene>(rfl::json::pretty);
}

}  // namespace toyscene
