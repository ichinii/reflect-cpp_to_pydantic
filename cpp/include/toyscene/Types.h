#pragma once

/// Value guards for the scene model.
///
/// Every alias here is an `rfl::Validator`, so the constraint is enforced by
/// reflect-cpp when JSON is parsed *and* exported into the JSON Schema, from
/// which the Pydantic models are generated. There is exactly one place to
/// change a bound.

#include <cmath>
#include <limits>
#include <sstream>
#include <string>

#include <rfl.hpp>

namespace toyscene {

/// Validation rule that rejects NaN and +/-infinity.
///
/// reflect-cpp's built-in numeric rules are written as "fail if out of range"
/// (`rfl::Minimum<0>` errors when `value < 0`). Every comparison involving NaN
/// is false, so NaN passes `Minimum`, `Maximum`, `ExclusiveMinimum`, ... and
/// every combination of them. This rule closes that hole and is therefore part
/// of every floating-point alias below.
struct Finite {
  template <class T>
  static rfl::Result<T> validate(T _value) noexcept {
    if (!std::isfinite(_value)) {
      std::ostringstream stream;
      stream << "Value expected to be finite, but got " << _value << ".";
      return rfl::error(stream.str());
    }
    return _value;
  }

  /// JSON Schema has no "is finite" keyword, and JSON has no NaN or infinity
  /// literal to exclude, so there is nothing to translate this rule into
  /// directly. The finite range [-DBL_MAX, DBL_MAX] is the exact equivalent for
  /// a consumer that reads bounds as "require in range" -- which is what
  /// Pydantic does, so the generated models reject NaN for the same reason this
  /// rule does. The schema post-processor keeps only the tightest bound per
  /// field, so these wide limits vanish wherever a real bound already exists.
  ///
  /// Note that `parsing::schema::ValidationType` is a closed variant: a custom
  /// rule can only express itself in terms of the rules reflect-cpp already
  /// knows. Returning something uninterpretable is not an option.
  template <class T>
  static rfl::parsing::schema::ValidationType to_schema() {
    return rfl::AllOf<rfl::Minimum<-std::numeric_limits<double>::max()>,
                      rfl::Maximum<std::numeric_limits<double>::max()>>::
        template to_schema<T>();
  }
};

/// A double that is neither NaN nor infinite.
using FiniteDouble = rfl::Validator<double, Finite>;

/// A double in (0, inf).
using PositiveDouble = rfl::Validator<double, rfl::ExclusiveMinimum<0.0>, Finite>;

/// A double in [0, inf).
using NonNegativeDouble = rfl::Validator<double, rfl::Minimum<0.0>, Finite>;

/// A double in [0, 1].
using UnitIntervalDouble =
    rfl::Validator<double, rfl::Minimum<0.0>, rfl::Maximum<1.0>, Finite>;

/// Number of rays to trace.
using RayCount = rfl::Validator<int, rfl::Minimum<1>, rfl::Maximum<10'000'000>>;

/// A string with at least one character.
using NonEmptyString = rfl::Validator<std::string, rfl::Size<rfl::Minimum<1>>>;

}  // namespace toyscene
