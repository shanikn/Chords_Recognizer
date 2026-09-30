// ChordChart's chord analysis: beats, bars, chords and key from mono 44.1 kHz audio.
//
// A port of the desktop Python pipeline (chordchart/pipeline.py and the madmom pieces it
// uses), stage by stage, checked against it with golden files (tests/golden_test.cpp).
// Platform-independent: the platform layer decodes audio to 16-bit PCM and gives the
// directory holding the models and tables (android/app/src/main/assets/analysis).

#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace chordchart {

struct AnalyzerConfig {
    std::string assets_dir;   // chord_features.onnx, key.onnx, beat_this_small0.onnx, *.npy
    int threads = 4;          // ONNX Runtime threads for Beat This! (the heaviest model)
    bool xnnpack = false;     // Beat This! on ONNX Runtime's XNNPACK execution provider
    bool ort_arena = false;   // onnxruntime's CPU memory arena: faster, but ~2x the peak
};

struct Key {
    std::string tonic;  // "Db", "F#", ...
    std::string mode;   // "major" / "minor"
    double confidence = 0;
};

struct ChordEvent {
    int beat = 0;         // 0-based beat in the bar
    double time = 0;      // seconds
    std::string symbol;   // "C", "Am", "N"
    std::string harte;    // "C:maj", "A:min", "N"
};

struct Bar {
    int index = 0;        // 0 = pickup, then 1, 2, ...
    double start = 0;
    double end = 0;
    std::vector<ChordEvent> chords;
};

struct Song {
    double duration = 0;
    Key key;
    double bpm = 0;
    int meter = 4;
    std::vector<Bar> bars;
    std::vector<std::string> warnings;
    std::map<std::string, double> timings;  // seconds per stage
    double elapsed = 0;
    // Every tracked beat (seconds) and its position in the bar (1 = downbeat), before the
    // chart is trimmed to the song: for scoring the beat tracking, and for playback later.
    std::vector<double> beat_times;
    std::vector<int> beat_positions;

    // The same fields and names as the desktop app's Song.to_json(), minus title/source
    // (the platform layer knows those), plus "beats": {"times", "positions"}.
    std::string to_json() const;
};

// A problem with the audio itself (too short, no steady beat): shown as-is to the user.
struct AnalysisError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

// The analysis cancelled through the `cancel` flag.
struct Cancelled : std::runtime_error {
    Cancelled() : std::runtime_error("cancelled") {}
};

// Per-stage progress: stage is one of "beats", "chords", "key", "bars", "chart"; fraction
// 0..1 of that stage (1 = done). Called from the analysis's threads (beats and chords run at
// the same time), so it must be thread-safe.
using Progress = std::function<void(const std::string& stage, double fraction)>;

class Analyzer {
public:
    explicit Analyzer(const AnalyzerConfig& config);
    ~Analyzer();
    Analyzer(const Analyzer&) = delete;
    Analyzer& operator=(const Analyzer&) = delete;

    // Mono 16-bit PCM at 44.1 kHz (what the desktop pipeline decodes with ffmpeg).
    // Setting *cancel stops the analysis at the next checkpoint (Cancelled is thrown).
    Song analyze(const int16_t* pcm, size_t samples, const Progress& progress = {},
                 const std::atomic<bool>* cancel = nullptr) const;

private:
    struct Impl;
    Impl* impl_;
};

}  // namespace chordchart
