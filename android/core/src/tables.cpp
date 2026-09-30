#include "tables.hpp"

#include <stdexcept>

#include "json.hpp"

namespace chordchart {

namespace {

std::string join(const std::string& dir, const std::string& name) {
    if (dir.empty()) return name;
    char last = dir.back();
    return (last == '/' || last == '\\') ? dir + name : dir + "/" + name;
}

Filterbank filterbank(const Array<float>& matrix) {
    Filterbank fb;
    fb.bins = matrix.rows();
    fb.bands = matrix.cols();
    fb.dense = matrix.data;
    fb.by_band.resize(fb.bands);
    for (size_t bin = 0; bin < fb.bins; ++bin)
        for (size_t band = 0; band < fb.bands; ++band)
            if (float w = matrix.at(bin, band); w != 0.0f) fb.by_band[band].emplace_back(static_cast<uint32_t>(bin), w);
    return fb;
}

SpectrogramSpec spectrogram(const std::string& dir, const json::Value& settings, const std::string& name) {
    SpectrogramSpec spec;
    spec.frame_size = static_cast<size_t>(settings["frame_size"].num());
    spec.hop_size = settings["hop_size"].num();
    spec.fps = settings["fps"].num();
    spec.log_mul = static_cast<float>(settings["log_mul"].num());
    spec.log_add = static_cast<float>(settings["log_add"].num());
    spec.filterbank = filterbank(load_npy<float>(join(dir, name + "_filterbank.npy")));
    if (spec.filterbank.bins != spec.frame_size / 2) throw std::runtime_error(name + ": filterbank size");
    return spec;
}

}  // namespace

Tables Tables::load(const std::string& dir) {
    Tables t;
    const json::Value settings = json::parse_file(join(dir, "settings.json"));

    t.chord_window = load_npy<double>(join(dir, "chord_window.npy")).data;
    t.chord = spectrogram(dir, settings["spectrogram"]["chord"], "chord");
    t.key = spectrogram(dir, settings["spectrogram"]["key"], "key");

    const auto& bt = settings["beat_this"];
    t.bt_sample_rate = static_cast<int>(bt["sample_rate"].num());
    t.bt_n_fft = static_cast<int>(bt["n_fft"].num());
    t.bt_hop = static_cast<int>(bt["hop"].num());
    t.bt_n_mels = static_cast<int>(bt["n_mels"].num());
    t.bt_fps = static_cast<int>(bt["fps"].num());
    t.bt_chunk = static_cast<int>(bt["chunk"].num());
    t.bt_border = static_cast<int>(bt["border"].num());
    t.bt_window = load_npy<float>(join(dir, "bt_window.npy")).data;
    t.bt_mel_filterbank = load_npy<float>(join(dir, "bt_mel_filterbank.npy"));

    const auto& dbn = settings["dbn"];
    t.dbn_fps = dbn["fps"].num();
    t.dbn_threshold = dbn["threshold"].num();
    t.dbn_correct = dbn["correct"].boolean();
    t.observation_lambda = dbn["observation_lambda"].num();
    for (const auto& meter : dbn["meters"].list()) {
        int m = static_cast<int>(meter.num());
        std::string p = "dbn" + std::to_string(m) + "_";
        BarHmm hmm;
        hmm.beats_per_bar = m;
        hmm.states = load_npy<uint32_t>(join(dir, p + "states.npy")).data;
        hmm.pointers = load_npy<uint32_t>(join(dir, p + "pointers.npy")).data;
        hmm.log_probs = load_npy<double>(join(dir, p + "log_probs.npy")).data;
        hmm.log_initial = load_npy<double>(join(dir, p + "log_initial.npy")).data;
        hmm.om_pointers = load_npy<uint32_t>(join(dir, p + "om_pointers.npy")).data;
        hmm.positions = load_npy<double>(join(dir, p + "positions.npy")).data;
        hmm.num_states = hmm.pointers.size() - 1;
        if (hmm.log_initial.size() != hmm.num_states || hmm.om_pointers.size() != hmm.num_states)
            throw std::runtime_error("DBN tables disagree for " + std::to_string(m) + "/4");
        t.hmms.push_back(std::move(hmm));
    }

    t.crf_fps = settings["crf"]["fps"].num();
    t.crf_pi = load_npy<float>(join(dir, "crf_pi.npy"));
    t.crf_A = load_npy<float>(join(dir, "crf_A.npy"));
    t.crf_W = load_npy<float>(join(dir, "crf_W.npy"));
    t.crf_c = load_npy<float>(join(dir, "crf_c.npy"));
    t.crf_tau = load_npy<float>(join(dir, "crf_tau.npy"));
    return t;
}

}  // namespace chordchart
