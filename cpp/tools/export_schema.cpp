/// Writes the three files the Python model generator consumes:
///
///   schema/scene.schema.json  -- the JSON Schema exactly as reflect-cpp emits it
///   schema/model_facts.json   -- the handful of things that schema cannot say
///   schema/named_unions.json  -- the schema of each registered tagged union,
///                                which reflect-cpp inlines rather than naming
///
/// All three are committed. `scripts/generate_models.sh` regenerates them and the
/// Pydantic models; `tests/test_schema_drift.py` fails if the committed copies
/// no longer match.

#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <string>

#include <rfl.hpp>
#include <rfl/json.hpp>

#include "NamedTypes.h"
#include "Reflectors.h"
#include "toyscene/Serialization.h"

namespace {

using namespace toyscene;

/// A buffer-backed array field: the dtype its buffer must have and the number
/// of dimensions its shape must have.
struct ArrayFact {
  std::string dtype;
  size_t ndim;
};

struct ModelFacts {
  rfl::Description<
      "Written by cpp/tools/export_schema.cpp. Do not edit by hand.",
      std::string>
      note = "";

  /// The $defs entry that reflect-cpp generates for Array<T>, i.e. the buffer
  /// reference object. Taken from reflect-cpp's own naming so a change in the
  /// library cannot silently desynchronise the post-processor.
  std::string bufferRefDefinition;

  /// struct -> field -> the default the C++ aggregate initializer applies.
  /// reflect-cpp's schema cannot express per-field defaults (see README), so
  /// the post-processor injects these.
  std::map<std::string, std::map<std::string, rfl::Generic>> defaults;

  /// struct -> field -> dtype and ndim of a buffer-backed array field.
  std::map<std::string, std::map<std::string, ArrayFact>> arrays;
};

/// The schema of every union in the registry, keyed by the name it should carry
/// in Python. Each value is `rfl::json::to_schema<T>()` verbatim: the `anyOf` as
/// the document root, with the member definitions carried along under `$defs`
/// so the post-processor can check them against the main schema.
std::map<std::string, rfl::Generic> namedUnionSchemas() {
  std::map<std::string, rfl::Generic> out;
  for (const auto& union_ : detail::namedUnions()) {
    auto parsed = rfl::json::read<rfl::Generic>(union_.schema);
    if (!parsed.has_value()) {
      throw std::runtime_error("Could not re-read the schema of union '" +
                               union_.name + "': " + parsed.error().what());
    }
    const auto [_, inserted] =
        out.emplace(union_.name, std::move(parsed).value());
    if (!inserted) {
      throw std::runtime_error("Union '" + union_.name +
                               "' is registered twice in NamedTypes.h.");
    }
  }
  return out;
}

/// A C++ value as the JSON it serializes to.
template <class T>
rfl::Generic asGeneric(const T& _value) {
  auto result = rfl::json::read<rfl::Generic>(rfl::json::write(_value));
  if (!result.has_value()) {
    throw std::runtime_error("Could not round-trip a default value: " +
                             result.error().what());
  }
  return std::move(result).value();
}

ModelFacts modelFacts() {
  // Prototypes exist so that every default below is *read off an actual C++
  // object*. Only the struct and field names are written out by hand here, and
  // the post-processor rejects a name that no longer appears in the schema.
  const auto probeEnergy = PhotonEnergy(ElectronVolt{.eV = 1.0});
  const auto probeElement = Element{.name = "probe",
                                    .area = RectArea{.width = 1.0, .height = 1.0},
                                    .behavior = Detector{}};
  const auto probeScene = Scene{.name = "probe",
                                .source = PointSource{.energy = probeEnergy},
                                .elements = {}};

  return ModelFacts{
      .bufferRefDefinition = rfl::parsing::make_type_name<Array<double>>(),
      .defaults =
          {
              {"PointSource",
               {{"divergence", asGeneric(PointSource{.energy = probeEnergy}.divergence)}}},
              {"Mirror", {{"reflectivity", asGeneric(Mirror{}.reflectivity)}}},
              {"Grating", {{"order", asGeneric(Grating{.lineDensity = 1.0}.order)}}},
              {"Element",
               {{"position", asGeneric(probeElement.position)},
                {"normal", asGeneric(probeElement.normal)}}},
              {"Scene", {{"numRays", asGeneric(probeScene.numRays)}}},
          },
      // ndim is the one fact here that C++ does not carry: Array<T> stores a
      // runtime shape, so the expected rank of each field lives in its
      // validate() method and must be restated for the schema.
      .arrays =
          {
              {"SampledSource",
               {{"positions", ArrayFact{.dtype = dtypeName<double>(), .ndim = 2}},
                {"weights", ArrayFact{.dtype = dtypeName<double>(), .ndim = 1}}}},
          },
  };
}

void write(const std::filesystem::path& _path, const std::string& _contents) {
  std::ofstream out(_path, std::ios::binary | std::ios::trunc);
  if (!out) {
    throw std::runtime_error("Could not open " + _path.string() + " for writing.");
  }
  out << _contents;
  if (!_contents.empty() && _contents.back() != '\n') {
    out << '\n';
  }
  if (!out) {
    throw std::runtime_error("Could not write " + _path.string() + ".");
  }
  std::cout << "wrote " << _path.string() << "\n";
}

}  // namespace

int main(int _argc, char** _argv) {
  try {
    const auto outDir =
        std::filesystem::path(_argc > 1 ? _argv[1] : "schema");
    std::filesystem::create_directories(outDir);
    write(outDir / "scene.schema.json", jsonSchema());
    write(outDir / "model_facts.json",
          rfl::json::write(modelFacts(), rfl::json::pretty));
    write(outDir / "named_unions.json",
          rfl::json::write(namedUnionSchemas(), rfl::json::pretty));
    return 0;
  } catch (const std::exception& e) {
    std::cerr << "export_schema failed: " << e.what() << "\n";
    return 1;
  }
}
