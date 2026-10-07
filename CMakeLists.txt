cmake_minimum_required(VERSION 3.23)
project(toyscene LANGUAGES CXX VERSION 0.1.0)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(TOYSCENE_BUILD_TESTS "Build the C++ ctest target" ON)

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
# reflect-cpp is not packaged in nixpkgs (checked: no `reflectcpp` /
# `reflect-cpp` attribute), so it is fetched at a pinned tag. Override with
# -DFETCHCONTENT_SOURCE_DIR_REFLECTCPP=/path/to/checkout to build against a
# local copy.
set(TOYSCENE_REFLECTCPP_TAG "v0.25.0" CACHE STRING "reflect-cpp git tag to build against")

# CMake reads FETCHCONTENT_BASE_DIR as a cache variable, never from the
# environment. Honour it here so the devShell can point every build tree -- the
# ctest one and the scikit-build-core one -- at a single clone instead of
# fetching and compiling reflect-cpp once per tree.
if(DEFINED ENV{FETCHCONTENT_BASE_DIR} AND NOT DEFINED FETCHCONTENT_BASE_DIR)
  set(FETCHCONTENT_BASE_DIR "$ENV{FETCHCONTENT_BASE_DIR}" CACHE PATH "Where FetchContent stores its downloads")
endif()

set(REFLECTCPP_JSON ON CACHE BOOL "" FORCE)
set(REFLECTCPP_USE_BUNDLED_DEPENDENCIES ON CACHE BOOL "" FORCE)  # bundled yyjson, no vcpkg
set(REFLECTCPP_INSTALL OFF CACHE BOOL "" FORCE)
set(REFLECTCPP_BUILD_TESTS OFF CACHE BOOL "" FORCE)

include(FetchContent)
FetchContent_Declare(reflectcpp
  GIT_REPOSITORY https://github.com/getml/reflect-cpp.git
  GIT_TAG ${TOYSCENE_REFLECTCPP_TAG}
  GIT_SHALLOW TRUE
  EXCLUDE_FROM_ALL
)
FetchContent_MakeAvailable(reflectcpp)

find_package(glm REQUIRED)

# ---------------------------------------------------------------------------
# The library: the scene model, its JSON/schema boundary and the simulation
# ---------------------------------------------------------------------------
add_library(toyscene_core STATIC
  cpp/src/Serialization.cpp
  cpp/src/Simulate.cpp
)
target_include_directories(toyscene_core
  PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}/cpp/include
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/cpp/src
)
# reflect-cpp and glm are accepted *public* dependencies: they appear in the
# public headers (rfl::Validator, rfl::TaggedUnion, glm::dvec3).
target_link_libraries(toyscene_core PUBLIC reflectcpp::reflectcpp glm::glm)
target_compile_options(toyscene_core PRIVATE -Wall -Wextra)

# ---------------------------------------------------------------------------
# Schema exporter
# ---------------------------------------------------------------------------
add_executable(toyscene_export_schema cpp/tools/export_schema.cpp)
target_link_libraries(toyscene_export_schema PRIVATE toyscene_core)
target_include_directories(toyscene_export_schema PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/cpp/src)

# ---------------------------------------------------------------------------
# C++ tests (ctest)
# ---------------------------------------------------------------------------
if(TOYSCENE_BUILD_TESTS AND NOT SKBUILD)
  enable_testing()
  add_executable(toyscene_cpp_tests cpp/tests/test_scene.cpp)
  target_link_libraries(toyscene_cpp_tests PRIVATE toyscene_core)
  target_include_directories(toyscene_cpp_tests PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/cpp/src)
  add_test(NAME toyscene_cpp_tests COMMAND toyscene_cpp_tests)
endif()

# ---------------------------------------------------------------------------
# Python extension (only when built through scikit-build-core)
# ---------------------------------------------------------------------------
if(SKBUILD)
  find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module)
  execute_process(
    COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
    OUTPUT_STRIP_TRAILING_WHITESPACE OUTPUT_VARIABLE nanobind_ROOT)
  find_package(nanobind CONFIG REQUIRED)

  nanobind_add_module(_core NB_STATIC bindings/module.cpp)
  target_link_libraries(_core PRIVATE toyscene_core)
  install(TARGETS _core LIBRARY DESTINATION toyscene)
endif()
