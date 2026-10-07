/// C++-side tests, run by ctest.
///
/// Three of them exist to pin down reflect-cpp behaviour that the design
/// depends on and that is not obvious from its documentation:
///
///   * the built-in numeric rules do not reject NaN;
///   * `rfl::Validator` *is* default-constructible, and the default throws when
///     the default value violates the rule;
///   * assigning to a `rfl::Validator` re-validates.

#include <cmath>
#include <cstdio>
#include <exception>
#include <limits>
#include <span>
#include <string>
#include <type_traits>
#include <vector>

#include <rfl.hpp>
#include <rfl/json.hpp>

#include "Reflectors.h"
#include "toyscene/Serialization.h"
#include "toyscene/Simulate.h"

namespace {

int gChecks = 0;
int gFailures = 0;

void record(bool _ok, const std::string& _what, int _line) {
  ++gChecks;
  if (!_ok) {
    ++gFailures;
    std::fprintf(stderr, "FAIL line %d: %s\n", _line, _what.c_str());
  }
}

#define CHECK(cond) record((cond), #cond, __LINE__)

#define CHECK_THROWS_WITH(expr, needle)                                       \
  do {                                                                        \
    bool threw = false;                                                       \
    std::string message;                                                      \
    try {                                                                     \
      /* static_cast keeps `T(name);` from parsing as a declaration. */       \
      static_cast<void>(expr);                                                \
    } catch (const std::exception& e) {                                       \
      threw = true;                                                           \
      message = e.what();                                                     \
    }                                                                         \
    record(threw, "expected " #expr " to throw", __LINE__);                   \
    record(threw && message.find(needle) != std::string::npos,                \
           std::string("expected the message to mention \"") + (needle) +     \
               "\", got: " + message,                                         \
           __LINE__);                                                         \
  } while (false)

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

using namespace toyscene;

// --- reflect-cpp behaviour this design relies on ---------------------------

void builtinNumericRulesDoNotRejectNaN() {
  // Every comparison against NaN is false, and the built-in rules are written
  // as "fail if out of range", so NaN passes all of them. This is the reason
  // the Finite rule exists.
  CHECK(rfl::Minimum<0.0>::validate(kNaN).has_value());
  CHECK(rfl::Maximum<1.0>::validate(kNaN).has_value());
  CHECK(rfl::ExclusiveMinimum<0.0>::validate(kNaN).has_value());
  CHECK((rfl::AllOf<rfl::Minimum<0.0>, rfl::Maximum<1.0>>::validate(kNaN).has_value()));

  // ... and the reason it is part of every floating-point alias.
  CHECK(!Finite::validate(kNaN).has_value());
  CHECK(!Finite::validate(std::numeric_limits<double>::infinity()).has_value());
  CHECK(Finite::validate(0.0).has_value());
  CHECK_THROWS_WITH(PositiveDouble(kNaN), "finite");
  CHECK_THROWS_WITH(UnitIntervalDouble(kNaN), "finite");
  CHECK_THROWS_WITH(FiniteDouble(kNaN), "finite");
}

void validatorIsDefaultConstructibleButMayThrow() {
  // It is default-constructible, so leaving a guarded field out of an aggregate
  // initializer compiles. The default constructor validates `T()`, so whether
  // that is a problem depends on the rule:
  static_assert(std::is_default_constructible_v<PositiveDouble>);
  static_assert(std::is_default_constructible_v<UnitIntervalDouble>);
  static_assert(std::is_default_constructible_v<NonEmptyString>);

  // 0.0 is inside [0, 1], so this one quietly succeeds ...
  CHECK(UnitIntervalDouble().value() == 0.0);
  // ... while these throw at run time, from a context that does not expect it.
  CHECK_THROWS_WITH(PositiveDouble(), "greater than 0");
  CHECK_THROWS_WITH(NonEmptyString(), "Size validation failed");

  // This is why neither rfl::DefaultIfMissing nor rfl::DefaultVal is used: both
  // route the parser through a path that starts from `T{}`, inside a function
  // marked noexcept.
}

void assigningToAValidatorRevalidates() {
  UnitIntervalDouble reflectivity = 0.5;
  reflectivity = 0.25;
  CHECK(reflectivity.value() == 0.25);

  CHECK_THROWS_WITH(reflectivity = 1.5, "less than or equal to 1");
  CHECK_THROWS_WITH(reflectivity = kNaN, "finite");
  // The old value survives a rejected assignment.
  CHECK(reflectivity.value() == 0.25);
}

// --- scene documents used below -------------------------------------------

std::string sampledSceneJson(const char* _weights = R"({"$buffer": 1, "dtype": "float64", "shape": [2]})") {
  return std::string(R"({
    "name": "test",
    "numRays": 4,
    "seed": 11,
    "source": {
      "type": "SampledSource",
      "positions": {"$buffer": 0, "dtype": "float64", "shape": [2, 3]},
      "weights": )") + _weights + R"(,
      "energy": {"type": "ElectronVolt", "eV": 250.0}
    },
    "elements": [
      {
        "name": "mirror",
        "position": [1.0, 2.0, 3.0],
        "normal": [0.0, 0.0, 1.0],
        "area": {"type": "RectArea", "width": 10.0, "height": 20.0},
        "behavior": {"type": "Mirror", "reflectivity": 0.9}
      }
    ]
  })";
}

std::vector<double> positionsData() {
  return {0.0, 0.0, 0.0, 1.0, 1.0, 1.0};
}

std::vector<BufferView> buffers(std::vector<double>& _positions,
                                std::vector<double>& _weights) {
  return {
      BufferView{.data = _positions.data(), .dtype = "float64", .shape = {2, 3}},
      BufferView{.data = _weights.data(), .dtype = "float64", .shape = {2}},
  };
}

// --- the JSON boundary ----------------------------------------------------

void validSceneRoundTrips() {
  auto positions = positionsData();
  std::vector<double> weights{1.0, 3.0};
  const auto views = buffers(positions, weights);

  const auto scene = fromJson(sampledSceneJson(), views);
  CHECK(scene.name.value() == "test");
  CHECK(scene.numRays.value() == 4);
  CHECK(scene.seed.has_value() && *scene.seed == 11);
  CHECK(scene.elements.size() == 1);
  CHECK(scene.elements[0].position == glm::dvec3(1.0, 2.0, 3.0));
  CHECK(scene.elements[0].normal == glm::dvec3(0.0, 0.0, 1.0));

  // Writing and reading back is a fixed point, which also exercises the
  // Reflector write path for glm::dvec3 and Array<double>.
  const auto once = toJson(scene);
  const auto twice = toJson(fromJson(once, views));
  CHECK(once == twice);
  CHECK(once.find(R"("position":[1.0,2.0,3.0])") != std::string::npos);
  CHECK(once.find(R"("$buffer":0)") != std::string::npos);
  CHECK(once.find(R"("$buffer":1)") != std::string::npos);
}

void rangeViolationsAreRejected() {
  auto positions = positionsData();
  std::vector<double> weights{1.0, 3.0};
  const auto views = buffers(positions, weights);

  auto with = [&](const std::string& _from, const std::string& _to) {
    auto document = sampledSceneJson();
    const auto at = document.find(_from);
    document.replace(at, _from.size(), _to);
    return document;
  };

  CHECK_THROWS_WITH(fromJson(with(R"("reflectivity": 0.9)", R"("reflectivity": 1.5)"), views),
                    "less than or equal to 1");
  CHECK_THROWS_WITH(fromJson(with(R"("eV": 250.0)", R"("eV": -1.0)"), views),
                    "greater than 0");
  CHECK_THROWS_WITH(fromJson(with(R"("numRays": 4)", R"("numRays": 0)"), views),
                    "greater than or equal to 1");
  CHECK_THROWS_WITH(fromJson(with(R"("name": "test")", R"("name": "")"), views),
                    "Size validation failed");
  // A non-finite scalar cannot even be spelled in standard JSON: yyjson
  // rejects the non-standard NaN/Infinity literals and refuses an exponent
  // that overflows a double. So this fails while parsing, before any guard
  // runs -- which is why the Finite rule earns its keep elsewhere (scenes built
  // in C++, assignment, bulk buffers, and the generated Pydantic models).
  CHECK_THROWS_WITH(fromJson(with(R"("width": 10.0)", R"("width": 1e999)"), views),
                    "infinity");
  CHECK_THROWS_WITH(fromJson(with(R"("width": 10.0)", R"("width": NaN)"), views),
                    "Could not parse document");

  // The field path is part of the message.
  bool mentionsPath = false;
  try {
    fromJson(with(R"("reflectivity": 0.9)", R"("reflectivity": 1.5)"), views);
  } catch (const SceneError& e) {
    mentionsPath = std::string(e.what()).find("elements") != std::string::npos;
  }
  CHECK(mentionsPath);
}

void missingFieldsAreRejected() {
  auto positions = positionsData();
  std::vector<double> weights{1.0, 3.0};
  const auto views = buffers(positions, weights);

  // A field with a C++ default initializer is still required in the JSON: see
  // validatorIsDefaultConstructibleButMayThrow above for why.
  auto document = sampledSceneJson();
  const std::string field = R"("numRays": 4,)";
  document.replace(document.find(field), field.size(), "");
  CHECK_THROWS_WITH(fromJson(document, views), "numRays");
}

void dvec3LengthIsChecked() {
  auto positions = positionsData();
  std::vector<double> weights{1.0, 3.0};
  const auto views = buffers(positions, weights);

  auto with = [&](const std::string& _normal) {
    auto document = sampledSceneJson();
    const std::string from = R"("normal": [0.0, 0.0, 1.0])";
    document.replace(document.find(from), from.size(), R"("normal": )" + _normal);
    return document;
  };

  CHECK_THROWS_WITH(fromJson(with("[0.0, 1.0]"), views), "normal");
  CHECK_THROWS_WITH(fromJson(with("[0.0, 0.0, 1.0, 0.0]"), views), "normal");
  CHECK_THROWS_WITH(fromJson(with("[0.0, 0.0, 0.5]"), views), "unit vector");
  CHECK_THROWS_WITH(fromJson(with("[1e999, 0.0, 0.0]"), views), "infinity");
}

void validateCatchesWhatJsonCannotExpress() {
  // A non-finite position cannot arrive through JSON (the reader refuses the
  // number), so this rule only ever fires for scenes assembled in C++.
  auto element = Element{.name = "e",
                         .area = RectArea{.width = 1.0, .height = 1.0},
                         .behavior = Detector{}};
  element.validate();  // the defaults are valid

  element.position = glm::dvec3(kNaN, 0.0, 0.0);
  CHECK_THROWS_WITH(element.validate(), "position: every component must be finite");

  element.position = glm::dvec3(0.0, 0.0, 0.0);
  element.normal = glm::dvec3(0.0, 0.0, 2.0);
  CHECK_THROWS_WITH(element.validate(), "unit vector");

  // Just inside and just outside the tolerance.
  element.normal = glm::dvec3(0.0, 0.0, 1.0 + 1e-10);
  element.validate();
  element.normal = glm::dvec3(0.0, 0.0, 1.0 + 1e-7);
  CHECK_THROWS_WITH(element.validate(), "unit vector");
}

void crossFieldRulesAreChecked() {
  auto positions = positionsData();
  std::vector<double> three{1.0, 2.0, 3.0};
  const std::vector<BufferView> views{
      BufferView{.data = positions.data(), .dtype = "float64", .shape = {2, 3}},
      BufferView{.data = three.data(), .dtype = "float64", .shape = {3}},
  };
  CHECK_THROWS_WITH(
      fromJson(sampledSceneJson(R"({"$buffer": 1, "dtype": "float64", "shape": [3]})"), views),
      "weights");

  std::vector<double> negative{1.0, -1.0};
  const std::vector<BufferView> withNegative{
      BufferView{.data = positions.data(), .dtype = "float64", .shape = {2, 3}},
      BufferView{.data = negative.data(), .dtype = "float64", .shape = {2}},
  };
  CHECK_THROWS_WITH(fromJson(sampledSceneJson(), withNegative), ">= 0");

  std::vector<double> nans{kNaN, 1.0};
  const std::vector<BufferView> withNaN{
      BufferView{.data = positions.data(), .dtype = "float64", .shape = {2, 3}},
      BufferView{.data = nans.data(), .dtype = "float64", .shape = {2}},
  };
  CHECK_THROWS_WITH(fromJson(sampledSceneJson(), withNaN), "finite");

  // The rank of positions is a non-schema rule, checked by validate() once the
  // buffer reference itself is consistent. Note the JSON field path prefix.
  std::vector<double> weights{1.0, 3.0};
  std::vector<double> flat{0.0, 1.0, 2.0, 3.0, 4.0, 5.0};
  const std::vector<BufferView> flatPositions{
      BufferView{.data = flat.data(), .dtype = "float64", .shape = {6}},
      BufferView{.data = weights.data(), .dtype = "float64", .shape = {2}},
  };
  auto flatDocument = sampledSceneJson();
  const std::string from = R"("shape": [2, 3])";
  flatDocument.replace(flatDocument.find(from), from.size(), R"("shape": [6])");
  CHECK_THROWS_WITH(fromJson(flatDocument, flatPositions), "source.positions");
}

void bufferReferencesAreChecked() {
  auto positions = positionsData();
  std::vector<double> weights{1.0, 3.0};
  const auto views = buffers(positions, weights);

  // Out of range.
  CHECK_THROWS_WITH(
      fromJson(sampledSceneJson(R"({"$buffer": 7, "dtype": "float64", "shape": [2]})"), views),
      "only 2 buffer(s)");

  // The declared shape disagrees with the buffer handed over.
  CHECK_THROWS_WITH(
      fromJson(sampledSceneJson(R"({"$buffer": 1, "dtype": "float64", "shape": [5]})"), views),
      "declares shape");

  // The declared dtype is not the one the field holds.
  CHECK_THROWS_WITH(
      fromJson(sampledSceneJson(R"({"$buffer": 1, "dtype": "float32", "shape": [2]})"), views),
      "float64");

  // The buffer itself has the wrong dtype.
  const std::vector<BufferView> wrongDtype{
      BufferView{.data = positions.data(), .dtype = "float64", .shape = {2, 3}},
      BufferView{.data = weights.data(), .dtype = "float32", .shape = {2}},
  };
  CHECK_THROWS_WITH(fromJson(sampledSceneJson(), wrongDtype), "instead of 'float64'");

  // No buffers at all.
  CHECK_THROWS_WITH(fromJson(sampledSceneJson(), {}), "only 0 buffer(s)");

  // Reading a scene without going through fromJson leaves no buffer table
  // installed, and an Array field then cannot be resolved at all.
  const auto bypassed = rfl::json::read<Scene>(sampledSceneJson());
  CHECK(!bypassed.has_value());
  CHECK(bypassed.has_value() ||
        std::string(bypassed.error().what()).find("no buffers were handed over") !=
            std::string::npos);
}

// --- the simulation -------------------------------------------------------

void simulationIsShapedAndDeterministic() {
  auto positions = positionsData();
  std::vector<double> weights{1.0, 3.0};
  const auto views = buffers(positions, weights);
  const auto scene = fromJson(sampledSceneJson(), views);

  const auto first = simulate(scene);
  const auto second = simulate(scene);
  CHECK(first.size() == 4 * kResultColumns);
  CHECK(first == second);
  CHECK(first[6] == 250.0);

  // A different seed gives different rays; the same seed does not.
  auto reseeded = scene;
  reseeded.seed = 12;
  CHECK(simulate(reseeded) != first);

  // The elements feed into the output, so a moved element is visible.
  auto moved = scene;
  moved.elements[0].position = glm::dvec3(100.0, 0.0, 0.0);
  CHECK(simulate(moved) != first);
}

void unitConversionsAreSane() {
  CHECK(std::abs(radians(Angle(Deg{180.0})) - 3.14159265358979) < 1e-12);
  CHECK(radians(Angle(Rad{1.5})) == 1.5);
  CHECK(electronVolts(PhotonEnergy(ElectronVolt{42.0})) == 42.0);
  // 1239.84 eV*nm / 4.9594 nm is about 250 eV.
  CHECK(std::abs(electronVolts(PhotonEnergy(Wavelength{4.959367937328}))- 250.0) < 1e-6);
}

void schemaCarriesTheConstraints() {
  const auto schema = jsonSchema();
  CHECK(schema.find(R"("minLength": 1)") != std::string::npos);
  CHECK(schema.find(R"("exclusiveMinimum": 0.0)") != std::string::npos);
  CHECK(schema.find(R"("maximum": 10000000)") != std::string::npos);
  CHECK(schema.find(R"("minItems": 3)") != std::string::npos);
  CHECK(schema.find(R"("maxItems": 3)") != std::string::npos);
  // The Finite rule reaches the schema as the finite double range.
  CHECK(schema.find("1.7976931348623157e") != std::string::npos);
  // std::optional is the only thing that makes a field non-required.
  CHECK(schema.find(R"("seed")") != std::string::npos);
}

}  // namespace

int main() {
  builtinNumericRulesDoNotRejectNaN();
  validatorIsDefaultConstructibleButMayThrow();
  assigningToAValidatorRevalidates();
  validSceneRoundTrips();
  rangeViolationsAreRejected();
  missingFieldsAreRejected();
  dvec3LengthIsChecked();
  validateCatchesWhatJsonCannotExpress();
  crossFieldRulesAreChecked();
  bufferReferencesAreChecked();
  simulationIsShapedAndDeterministic();
  unitConversionsAreSane();
  schemaCarriesTheConstraints();

  std::printf("%d checks, %d failures\n", gChecks, gFailures);
  return gFailures == 0 ? 0 : 1;
}
