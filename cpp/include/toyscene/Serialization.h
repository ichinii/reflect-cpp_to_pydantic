#pragma once

/// The JSON boundary: parse, write, and export the schema.
///
/// Bulk arrays never travel inside the JSON. A JSON field of type `Array<T>`
/// holds a *reference* to a buffer passed alongside the document:
///
///     {"$buffer": 0, "dtype": "float64", "shape": [100000, 3]}
///
/// `fromJson` resolves those references against the `buffers` argument.

#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include "toyscene/Scene.h"

namespace toyscene {

/// Anything wrong with a scene: malformed JSON, a violated value guard, a
/// broken cross-field rule, a bad buffer reference. The message names the JSON
/// field path wherever the information is available.
class SceneError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

/// One out-of-band bulk buffer. Borrowed: `data` must stay valid for the
/// duration of the `fromJson` call, or `owner` must keep it alive for as long
/// as the resulting Scene does.
struct BufferView {
  const void* data = nullptr;
  std::string dtype;            // numpy dtype name, e.g. "float64"
  std::vector<size_t> shape;
  std::shared_ptr<const void> owner{};
};

/// Parses and fully validates a scene. Throws SceneError.
Scene fromJson(std::string_view _json, std::span<const BufferView> _buffers);

/// Serializes a scene. Arrays come out as buffer references numbered in
/// traversal order (0, 1, ...), which need not match the numbering the scene
/// was read with.
std::string toJson(const Scene& _scene);

/// The JSON Schema for `Scene`, straight out of reflect-cpp.
std::string jsonSchema();

/// Runs every `validate()` in the scene, prefixing failures with their JSON
/// field path. Called by `fromJson`; exposed so scenes built in C++ can be
/// checked too. Throws SceneError.
void validate(const Scene& _scene);

/// The numpy dtype name expected for `Array<T>` buffers.
template <class T>
constexpr const char* dtypeName();

template <>
constexpr const char* dtypeName<double>() {
  return "float64";
}

}  // namespace toyscene
