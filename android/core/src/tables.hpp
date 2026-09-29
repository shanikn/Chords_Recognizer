// The analysis's constant tables (android/tools/export_tables.py writes them from the
// Python pipeline's own code): windows, filterbanks, the DBN's bar HMMs, the CRF.

#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "npy.hpp"

namespace chordchart {

// A filterbank (FFT bins x bands) stored by band: each band's non-zero bins, in bin order.
struct Filterbank {
    size_t bins = 0;
    size_t bands = 0;
    std::vector<std::vector<std::pair<uint32_t, float>>> by_band;
    std::vector<float> dense;  // bins x bands, for code that wants the matrix
};

struct SpectrogramSpec {
    size_t frame_size = 0;
    double hop_size = 0;
    double fps = 0;
    float log_mul = 1, log_add = 1;
    Filterbank filterbank;
};

struct BarHmm {
    int beats_per_bar = 0;
    size_t num_states = 0;
    std::vector<uint32_t> states;      // predecessor of each transition
    std::vector<uint32_t> pointers;    // transitions of state s: [pointers[s], pointers[s+1])
    std::vector<double> log_probs;     // log-probability of each transition
    std::vector<double> log_initial;   // per state
    std::vector<uint32_t> om_pointers; // per state: which observation density (0, 1, 2)
    std::vector<double> positions;     // per state: position in the bar (beats)
};

struct Tables {
    std::vector<double> chord_window;  // 8192: np.hanning / 32767 (int16 audio)
    SpectrogramSpec chord, key;

    int bt_sample_rate = 22050, bt_n_fft = 1024, bt_hop = 441, bt_n_mels = 128;
    int bt_fps = 50, bt_chunk = 1500, bt_border = 6;
    std::vector<float> bt_window;          // 1024
    Array<float> bt_mel_filterbank;        // 513 x 128

    double dbn_fps = 50, dbn_threshold = 0.05, observation_lambda = 16;
    bool dbn_correct = true;
    std::vector<BarHmm> hmms;              // 3/4, 4/4

    double crf_fps = 10;
    Array<float> crf_pi, crf_A, crf_W, crf_c, crf_tau;

    static Tables load(const std::string& dir);
};

}  // namespace chordchart
