#pragma once

/// Private: `rfl::Reflector` specializations for the two types in the scene
/// model that reflect-cpp cannot reflect on its own.
///
/// There are deliberately *no* specializations for `rfl::Validator` or
/// `rfl::TaggedUnion` -- reflect-cpp handles both natively, including in the
/// exported schema.

#include <array>
#include <cstddef>
#include <span>
#include <string>
#include <vector>

#include <glm/vec3.hpp>
#include <rfl.hpp>

#include "toyscene/Array.h"
#include "toyscene/Serialization.h"

namespace toyscene::detail {

/// `rfl::Reflector` specializations are stateless, but resolving a
/// `{"$buffer": i}` reference needs the buffer table of the *current*
/// `fromJson` call. These RAII guards install that context in a thread_local
/// slot for the duration of the call; the slot is private to this header.
class ReadBufferScope {
 public:
  explicit ReadBufferScope(std::span<const BufferView> _buffers);
  ~ReadBufferScope();
  ReadBufferScope(const ReadBufferScope&) = delete;
  ReadBufferScope(ReadBufferScope&&) = delete;
  ReadBufferScope& operator=(const ReadBufferScope&) = delete;
  ReadBufferScope& operator=(ReadBufferScope&&) = delete;

 private:
  std::span<const BufferView> previous_;
  bool previouslyActive_;
};

/// The write-side counterpart: hands out the indices `toJson` emits.
class WriteBufferScope {
 public:
  WriteBufferScope();
  ~WriteBufferScope();
  WriteBufferScope(const WriteBufferScope&) = delete;
  WriteBufferScope(WriteBufferScope&&) = delete;
  WriteBufferScope& operator=(const WriteBufferScope&) = delete;
  WriteBufferScope& operator=(WriteBufferScope&&) = delete;

 private:
  size_t previous_;
  bool previouslyActive_;
};

/// Looks up the buffer a `$buffer` index refers to. Throws SceneError when
/// there is no active scope or the index is out of range.
const BufferView& readBuffer(size_t _index);

/// The index to emit for the next array written. Throws SceneError when there
/// is no active scope.
size_t nextWriteIndex();

}  // namespace toyscene::detail

namespace rfl {

/// `glm::dvec3` as `[x, y, z]`.
///
/// The schema falls out of `ReflType` on its own: reflect-cpp turns
/// `std::array<double, 3>` into `FixedSizeTypedArray`, which the JSON Schema
/// writer emits as
/// `{"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}`
/// -- the length really is pinned to 3, so no post-processing is needed here.
template <>
struct Reflector<glm::dvec3> {
  using ReflType = std::array<double, 3>;

  static glm::dvec3 to(const ReflType& _v) noexcept {
    return glm::dvec3(_v[0], _v[1], _v[2]);
  }

  static ReflType from(const glm::dvec3& _v) {
    return ReflType{_v.x, _v.y, _v.z};
  }
};

/// `Array<T>` as a reference to an out-of-band buffer:
/// `{"$buffer": 0, "dtype": "float64", "shape": [100000, 3]}`.
///
/// The dtype and shape are carried in the JSON so that the document is
/// self-describing and can be checked against the buffer that was actually
/// handed over -- a mismatch is a hard error rather than a reinterpretation of
/// somebody's memory.
template <class T>
struct Reflector<toyscene::Array<T>> {
  struct ReflType {
    rfl::Rename<"$buffer", size_t> buffer;
    std::string dtype;
    std::vector<size_t> shape;
  };

  static toyscene::Array<T> to(const ReflType& _ref) {
    const auto expected = std::string(toyscene::dtypeName<T>());
    if (_ref.dtype != expected) {
      throw toyscene::SceneError("declares dtype '" + _ref.dtype +
                                 "', but this field holds '" + expected + "'");
    }
    const auto& view = toyscene::detail::readBuffer(_ref.buffer());
    if (view.dtype != expected) {
      throw toyscene::SceneError("refers to buffer " +
                                 std::to_string(_ref.buffer()) + ", which has dtype '" +
                                 view.dtype + "' instead of '" + expected + "'");
    }
    if (view.shape != _ref.shape) {
      throw toyscene::SceneError(
          "declares shape " + shapeToString(_ref.shape) + ", but buffer " +
          std::to_string(_ref.buffer()) + " has shape " + shapeToString(view.shape));
    }
    return toyscene::Array<T>::view(static_cast<const T*>(view.data),
                                    view.shape, view.owner);
  }

  static ReflType from(const toyscene::Array<T>& _array) {
    return ReflType{.buffer = toyscene::detail::nextWriteIndex(),
                    .dtype = std::string(toyscene::dtypeName<T>()),
                    .shape = _array.shape()};
  }

 private:
  static std::string shapeToString(const std::vector<size_t>& _shape) {
    std::string out = "(";
    for (size_t i = 0; i < _shape.size(); ++i) {
      out += (i ? ", " : "") + std::to_string(_shape[i]);
    }
    return out + ")";
  }
};

}  // namespace rfl
