// Beat This! on ONNX Runtime: the log-mel spectrogram in 1500-frame chunks overlapping by
// 6 frames at each side, each chunk's border frames dropped and the first chunk winning
// where chunks overlap (chordchart/beat_this.py BeatThisModel.logits).

#pragma once

#include <vector>

#include "npy.hpp"
#include "onnx.hpp"
#include "tables.hpp"

namespace chordchart {

struct Logits {
    std::vector<float> beat, down;  // per frame at Tables::bt_fps
};

Logits beat_this_logits(const Array<float>& mel, const OnnxModel& model, const Tables& tables);

}  // namespace chordchart
