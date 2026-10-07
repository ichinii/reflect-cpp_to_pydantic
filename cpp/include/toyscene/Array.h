#pragma once

#include <cstddef>
#include <memory>
#include <numeric>
#include <span>
#include <stdexcept>
#include <utility>
#include <vector>

namespace toyscene {

/// Shared, immutable bulk data with a shape.
///
/// Copies are cheap -- they share the buffer. The buffer is either owned by the
/// Array (`fromVector`) or borrowed from somewhere else (`view`), which is how
/// a numpy array handed in from Python reaches the simulation without a copy.
/// There is deliberately no mutable access: the scene is a description, not a
/// workspace.
template <class T>
class Array {
 public:
  Array() = default;

  /// Takes ownership of `_values`; `_shape` must describe exactly its size.
  static Array fromVector(std::vector<T> _values, std::vector<size_t> _shape) {
    const auto expected = sizeOf(_shape);
    if (expected != _values.size()) {
      throw std::invalid_argument(
          "Array::fromVector: shape describes " + std::to_string(expected) +
          " elements, but the vector holds " + std::to_string(_values.size()) +
          ".");
    }
    auto holder = std::make_shared<std::vector<T>>(std::move(_values));
    const T* data = holder->data();
    return Array(std::shared_ptr<const T[]>(std::move(holder), data),
                 std::move(_shape), expected);
  }

  /// Takes ownership of `_values` as a 1-d array.
  static Array fromVector(std::vector<T> _values) {
    const auto size = _values.size();
    return fromVector(std::move(_values), std::vector<size_t>{size});
  }

  /// Aliases memory owned by somebody else. `_owner` keeps that somebody alive
  /// for as long as the Array exists; pass an empty shared_ptr if the caller
  /// guarantees the memory outlives the Array (that is what the nanobind
  /// boundary does -- the input arrays only have to survive the call).
  static Array view(const T* _data, std::vector<size_t> _shape,
                    std::shared_ptr<const void> _owner) {
    const auto size = sizeOf(_shape);
    return Array(std::shared_ptr<const T[]>(std::move(_owner), _data),
                 std::move(_shape), size);
  }

  std::span<const T> values() const noexcept {
    return std::span<const T>(data_.get(), size_);
  }

  const std::vector<size_t>& shape() const noexcept { return shape_; }
  size_t ndim() const noexcept { return shape_.size(); }
  size_t size() const noexcept { return size_; }
  bool empty() const noexcept { return size_ == 0; }

 private:
  Array(std::shared_ptr<const T[]> _data, std::vector<size_t> _shape,
        size_t _size)
      : data_(std::move(_data)), shape_(std::move(_shape)), size_(_size) {}

  static size_t sizeOf(const std::vector<size_t>& _shape) {
    if (_shape.empty()) {
      return 0;
    }
    return std::accumulate(_shape.begin(), _shape.end(), size_t{1},
                           std::multiplies<size_t>());
  }

  std::shared_ptr<const T[]> data_{};
  std::vector<size_t> shape_{};
  size_t size_{0};
};

}  // namespace toyscene
