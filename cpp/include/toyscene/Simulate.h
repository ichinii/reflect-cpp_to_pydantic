#pragma once

#include <cstddef>
#include <vector>

#include "toyscene/Scene.h"

namespace toyscene {

/// Columns of a result row.
inline constexpr size_t kResultColumns = 7;  // x, y, z, dx, dy, dz, energy_eV

/// Traces `scene.numRays` rays. Deterministic for a given `scene.seed`.
/// Returns `numRays * kResultColumns` doubles in row-major order.
std::vector<double> simulate(const Scene& _scene);

}  // namespace toyscene
