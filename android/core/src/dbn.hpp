// madmom's DBNDownBeatTrackingProcessor with Beat This!'s settings: from beat and
// downbeat logits to beat times and their positions in the bar.
//
// - dbn_activations: chordchart/beat_this.py dbn_activations (sigmoid, kept off 0 and 1)
// - threshold_activations: madmom (drops quiet frames at both ends; note its quirk:
//   `idx.any()` is False when frame 0 is the only one above the threshold)
// - each bar HMM (3/4, 4/4) decoded with an exact low-memory Viterbi (see viterbi below)
// - the best meter by log-probability; beats refined to the activation peak in each beat
//   range (madmom's `correct`)

#pragma once

#include <cstdint>
#include <functional>
#include <vector>

#include "tables.hpp"

namespace chordchart {

struct Activations {
    std::vector<double> beat, down;  // [beat-but-not-downbeat, downbeat] probabilities
    size_t size() const { return beat.size(); }
};

Activations dbn_activations(const std::vector<float>& beat_logits, const std::vector<float>& down_logits);

struct ViterbiResult {
    std::vector<uint32_t> path;  // empty if no finite path
    double log_probability = 0;
    size_t checkpoint_bytes = 0, segment_bytes = 0;  // memory actually used
};

// madmom's HiddenMarkovModel.viterbi, same arithmetic and tie-breaking (a later predecessor
// wins only if strictly better), so the same path; memory ~ frames/K + K rows instead of
// one uint32 per frame and state: 1-byte predecessor indices, and only every K-th frame's
// scores kept going forward (the segments are recomputed while backtracking).
ViterbiResult viterbi(const BarHmm& hmm, const Activations& activations, size_t first, size_t count,
                      double observation_lambda, size_t checkpoint_every = 0);

struct Tracked {
    std::vector<double> times;  // seconds
    std::vector<int> positions; // 1 = downbeat
};

// run_in_parallel(tasks): runs the per-meter decodings (the analyzer passes threads)
using Parallel = std::function<void(std::vector<std::function<void()>>&)>;

Tracked track_beats(const Activations& activations, const Tables& tables, const Parallel& parallel);

}  // namespace chordchart
