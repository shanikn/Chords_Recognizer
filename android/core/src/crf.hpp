// madmom's chord CRF (ConditionalRandomField.process) and majmin_targets_to_chord_labels:
// from the CNN's 128 features per frame (10 fps) to chord segments ("C:maj", "A:min", "N").

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "npy.hpp"
#include "tables.hpp"

namespace chordchart {

struct Segment {
    double start = 0, end = 0;
    std::string label;
};

// float32 arithmetic in madmom's order; the per-frame dot product (numpy calls BLAS) is
// accumulated in float64 and rounded, so it can only be closer to the exact value.
std::vector<uint32_t> crf_decode(const Array<float>& features, const Tables& tables);

std::vector<Segment> chord_segments(const std::vector<uint32_t>& path, double fps);

}  // namespace chordchart
