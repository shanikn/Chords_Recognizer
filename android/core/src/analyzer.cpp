#include "chordchart/analyzer.hpp"

#include <chrono>
#include <cstdio>
#include <exception>
#include <mutex>
#include <thread>

#include "beat_this.hpp"
#include "chart.hpp"
#include "crf.hpp"
#include "dbn.hpp"
#include "onnx.hpp"
#include "resample.hpp"
#include "spectrogram.hpp"
#include "tables.hpp"

namespace chordchart {

namespace {

std::string join(const std::string& dir, const std::string& name) {
    if (dir.empty()) return name;
    char last = dir.back();
    return (last == '/' || last == '\\') ? dir + name : dir + "/" + name;
}

double seconds_since(std::chrono::steady_clock::time_point t) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t).count();
}

void run_parallel(std::vector<std::function<void()>>& tasks) {
    std::vector<std::thread> threads;
    std::vector<std::exception_ptr> errors(tasks.size());
    for (size_t i = 1; i < tasks.size(); ++i)
        threads.emplace_back([&, i] {
            try {
                tasks[i]();
            } catch (...) {
                errors[i] = std::current_exception();
            }
        });
    if (!tasks.empty()) {
        try {
            tasks[0]();
        } catch (...) {
            errors[0] = std::current_exception();
        }
    }
    for (auto& t : threads) t.join();
    for (auto& e : errors)
        if (e) std::rethrow_exception(e);
}

}  // namespace

struct Analyzer::Impl {
    AnalyzerConfig config;
    Tables tables;
    std::unique_ptr<OnnxModel> beat_this, chord_features, key;
};

Analyzer::Analyzer(const AnalyzerConfig& config) : impl_(new Impl) {
    impl_->config = config;
    impl_->tables = Tables::load(config.assets_dir);
    // Beat This! gets the configured threads (it is most of the time); the chord and key
    // CNNs run beside it on two threads.
    impl_->beat_this = std::make_unique<OnnxModel>(join(config.assets_dir, "beat_this_small0.onnx"),
                                                   OnnxOptions{config.threads, config.xnnpack, config.ort_arena});
    impl_->chord_features = std::make_unique<OnnxModel>(join(config.assets_dir, "chord_features.onnx"),
                                                        OnnxOptions{2, false, config.ort_arena});
    impl_->key = std::make_unique<OnnxModel>(join(config.assets_dir, "key.onnx"), OnnxOptions{2, false, config.ort_arena});
}

Analyzer::~Analyzer() { delete impl_; }

Song Analyzer::analyze(const int16_t* pcm, size_t samples, const Progress& progress) const {
    const auto& t = impl_->tables;
    const auto began = std::chrono::steady_clock::now();
    std::mutex lock;
    std::map<std::string, double> timings;
    auto timed = [&](const std::string& key, auto&& fn) {
        const auto start = std::chrono::steady_clock::now();
        auto result = fn();
        std::lock_guard<std::mutex> guard(lock);
        timings[key] = seconds_since(start);
        return result;
    };
    auto report = [&](const std::string& stage, double fraction) {
        if (progress) {
            std::lock_guard<std::mutex> guard(lock);
            progress(stage, fraction);
        }
    };
    const double duration = static_cast<double>(samples) / 44100.0;
    if (duration < 5.0) {  // fetch.MIN_DURATION
        char message[80];
        std::snprintf(message, sizeof message, "audio is only %.1f s long; need at least 5 s", duration);
        throw AnalysisError(message);
    }

    // chords and key, beside the beat tracker
    std::vector<Segment> segments;
    Key key;
    std::exception_ptr side_error;
    std::thread side([&] {
        try {
            report("recognizing chords", 0.05);
            auto chord_spec = timed("chord_spectrogram", [&] { return log_filtered_spectrogram(pcm, samples, t.chord, t.chord_window); });
            auto features = timed("chords", [&] {
                auto out = impl_->chord_features->run(chord_spec.data.data(),
                                                      {1, static_cast<int64_t>(chord_spec.rows()), static_cast<int64_t>(chord_spec.cols())});
                Array<float> f;
                f.shape = {static_cast<size_t>(out[0].shape[1]), static_cast<size_t>(out[0].shape[2])};
                f.data = std::move(out[0].data);
                return f;
            });
            segments = timed("crf", [&] { return chord_segments(crf_decode(features, t), t.crf_fps); });
            report("detecting key", 0.15);
            auto key_spec = timed("key_spectrogram", [&] { return log_filtered_spectrogram(pcm, samples, t.key, t.chord_window); });
            key = timed("key", [&] {
                auto out = impl_->key->run(key_spec.data.data(),
                                           {1, static_cast<int64_t>(key_spec.rows()), static_cast<int64_t>(key_spec.cols())});
                return key_from_probabilities(out[0].data);
            });
        } catch (...) {
            side_error = std::current_exception();
        }
    });

    Tracked tracked;
    std::exception_ptr main_error;
    try {
        report("tracking beats", 0.02);
        auto resampled = timed("resample", [&] { return resample_int16(pcm, samples, 44100.0, t.bt_sample_rate); });
        auto mel = timed("mel", [&] { return log_mel(resampled.data(), resampled.size(), t); });
        resampled.clear();
        resampled.shrink_to_fit();
        auto logits = timed("beats", [&] { return beat_this_logits(mel, *impl_->beat_this, t); });
        mel.data.clear();
        mel.data.shrink_to_fit();
        report("finding bars", 0.8);
        tracked = timed("dbn", [&] { return track_beats(dbn_activations(logits.beat, logits.down), t, run_parallel); });
    } catch (...) {
        main_error = std::current_exception();
    }
    side.join();
    if (main_error) std::rethrow_exception(main_error);
    if (side_error) std::rethrow_exception(side_error);

    report("building chart", 0.95);
    Song song = timed("chart", [&] { return build_chart(tracked, segments, key, duration); });
    song.timings = timings;
    song.elapsed = seconds_since(began);
    report("done", 1.0);
    return song;
}

}  // namespace chordchart
