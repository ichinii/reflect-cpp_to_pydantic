/// `toyscene._core`: the only place Python and C++ touch.
///
/// The scene crosses as JSON; bulk arrays cross as numpy buffers, in both
/// directions without a copy.

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <cstddef>
#include <string>
#include <utility>
#include <vector>

#include "toyscene/Serialization.h"
#include "toyscene/Simulate.h"

namespace nb = nanobind;

static_assert(toyscene::kResultColumns == 7,
              "The declared result shape below has to match kResultColumns.");

namespace {

/// An incoming bulk buffer: read-only, C-contiguous, in host memory. The dtype
/// is deliberately *not* constrained here -- a mismatch is reported by the C++
/// side, which knows which field the buffer was referenced from and can say so.
using InputBuffer = nb::ndarray<nb::ro, nb::c_contig, nb::device::cpu>;

/// The result: (numRays, 7) float64 handed to numpy without a copy.
using ResultArray = nb::ndarray<nb::numpy, double, nb::shape<-1, 7>>;

/// The numpy name of a DLPack dtype, for error messages and the dtype check.
std::string numpyDtypeName(const nb::dlpack::dtype& _dtype) {
  const auto bits = std::to_string(static_cast<unsigned>(_dtype.bits));
  switch (static_cast<nb::dlpack::dtype_code>(_dtype.code)) {
    case nb::dlpack::dtype_code::Bool:
      return "bool";
    case nb::dlpack::dtype_code::Int:
      return "int" + bits;
    case nb::dlpack::dtype_code::UInt:
      return "uint" + bits;
    case nb::dlpack::dtype_code::Float:
      return "float" + bits;
    case nb::dlpack::dtype_code::Complex:
      return "complex" + bits;
    default:
      return "dtype(code=" + std::to_string(static_cast<unsigned>(_dtype.code)) +
             ", bits=" + bits + ")";
  }
}

ResultArray simulateJson(const std::string& _json,
                         const std::vector<InputBuffer>& _buffers) {
  // The buffers are only described here, never copied. They stay owned by the
  // caller's numpy arrays, which the argument tuple keeps alive for the whole
  // call -- which is all `fromJson` and `simulate` need.
  std::vector<toyscene::BufferView> views;
  views.reserve(_buffers.size());
  for (const auto& buffer : _buffers) {
    std::vector<size_t> shape(buffer.ndim());
    for (size_t i = 0; i < buffer.ndim(); ++i) {
      shape[i] = static_cast<size_t>(buffer.shape(i));
    }
    views.push_back(toyscene::BufferView{.data = buffer.data(),
                                         .dtype = numpyDtypeName(buffer.dtype()),
                                         .shape = std::move(shape)});
  }

  std::vector<double> rows;
  size_t rayCount = 0;
  {
    // Neither parsing nor simulating touches the Python heap.
    const nb::gil_scoped_release unlocked;
    const auto scene = toyscene::fromJson(_json, views);
    rayCount = static_cast<size_t>(scene.numRays.value());
    rows = toyscene::simulate(scene);
  }

  // Move the result to the heap and let numpy own it through a capsule, so the
  // array Python receives points straight at the simulation's output.
  auto* owned = new std::vector<double>(std::move(rows));
  const nb::capsule owner(owned, [](void* _p) noexcept {
    delete static_cast<std::vector<double>*>(_p);
  });
  return ResultArray(owned->data(), {rayCount, toyscene::kResultColumns}, owner);
}

}  // namespace

NB_MODULE(_core, m) {
  m.doc() = "Native core of toyscene: scene parsing, validation and simulation.";

  // toyscene.SceneError is this type; deriving from ValueError keeps it
  // catchable alongside pydantic.ValidationError.
  [[maybe_unused]] const nb::exception<toyscene::SceneError> sceneError(
      m, "SceneError", PyExc_ValueError);

  m.attr("RESULT_COLUMNS") = toyscene::kResultColumns;

  m.def("simulate_json", &simulateJson, nb::arg("json"),
        nb::arg("buffers").noconvert(),
        R"doc(Parse, validate and simulate a scene.

`json` is a complete scene document; `buffers` holds the arrays its `$buffer`
references point at, in index order. The buffers are read in place -- they are
not copied, and must be C-contiguous (a non-contiguous array is rejected rather
than quietly converted).

Returns a (numRays, 7) float64 array of `x, y, z, dx, dy, dz, energy_eV` rows,
also without a copy. Raises SceneError.)doc");

  m.def(
      "scene_to_json",
      [](const std::string& _json, const std::vector<InputBuffer>& _buffers) {
        std::vector<toyscene::BufferView> views;
        views.reserve(_buffers.size());
        for (const auto& buffer : _buffers) {
          std::vector<size_t> shape(buffer.ndim());
          for (size_t i = 0; i < buffer.ndim(); ++i) {
            shape[i] = static_cast<size_t>(buffer.shape(i));
          }
          views.push_back(toyscene::BufferView{
              .data = buffer.data(),
              .dtype = numpyDtypeName(buffer.dtype()),
              .shape = std::move(shape)});
        }
        const nb::gil_scoped_release unlocked;
        return toyscene::toJson(toyscene::fromJson(_json, views));
      },
      nb::arg("json"), nb::arg("buffers").noconvert(),
      R"doc(Round-trip a scene document through the C++ model.

Exists for tests: it proves that JSON -> C++ -> JSON preserves the scene.
Buffer indices in the output are renumbered in traversal order.)doc");

  m.def("json_schema", &toyscene::jsonSchema,
        "The JSON Schema for Scene, as reflect-cpp exports it.");
}
