// Compares every stage of the C++ core with the Python pipeline's golden files
// (android/tools/make_golden.py). Each stage runs on the *Python* stage's input, so an
// upstream difference can't hide or cause a downstream failure:
//
//   numeric stages (spectrograms, resampling, mel, logits, CNNs): max difference reported,
//     must stay under the tolerance;
//   decision stages (Viterbi paths, tracked beats, CRF path, key, the chart): identical.
//
// Then the whole analysis from the PCM, end to end, with its agreement to Python's Song.
//
//   golden_test <assets dir> <golden dir> [song ...] [--xnnpack] [--threads N]

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

#include "beat_this.hpp"
#include "chart.hpp"
#include "chordchart/analyzer.hpp"
#include "crf.hpp"
#include "dbn.hpp"
#include "json.hpp"
#include "npy.hpp"
#include "onnx.hpp"
#include "resample.hpp"
#include "spectrogram.hpp"
#include "tables.hpp"

using namespace chordchart;
namespace fs = std::filesystem;

namespace {

int failures = 0;

void check(bool ok, const std::string& song, const std::string& what, const std::string& detail) {
    std::printf("  %-4s %-34s %s\n", ok ? "ok" : "FAIL", what.c_str(), detail.c_str());
    if (!ok) {
        ++failures;
        std::fprintf(stderr, "FAIL %s: %s %s\n", song.c_str(), what.c_str(), detail.c_str());
    }
}

template <typename A, typename B>
double max_abs(const std::vector<A>& a, const std::vector<B>& b) {
    double m = 0;
    for (size_t i = 0; i < std::min(a.size(), b.size()); ++i)
        m = std::max(m, std::abs(static_cast<double>(a[i]) - static_cast<double>(b[i])));
    return m;
}

template <typename T>
double max_abs_value(const std::vector<T>& a) {
    double m = 0;
    for (auto v : a) m = std::max(m, std::abs(static_cast<double>(v)));
    return m;
}

std::string fmt(const char* f, double v) {
    char buf[64];
    std::snprintf(buf, sizeof buf, f, v);
    return buf;
}

// the chord (harte) sounding at time t in a chart, "" outside the bars
std::string chord_at(const json::List& bars, double t) {
    for (const auto& bar : bars) {
        if (t < bar["start"].num() || t >= bar["end"].num()) continue;
        std::string current;
        for (const auto& c : bar["chords"].list())
            if (c["time"].num() <= t + 1e-9) current = c["harte"].str();
        if (current.empty() && !bar["chords"].list().empty()) current = bar["chords"][0]["harte"].str();
        return current;
    }
    return "";
}

json::List bars_json(const Song& song) { return json::parse(song.to_json())["bars"].list(); }

struct Agreement {
    double chords = 0;  // share of 0.1 s steps with the same chord
    double bars = 0;    // share of Python's bar lines matched within 25 ms
    bool identical = false;
};

Agreement agreement(const json::List& ours, const json::List& theirs, double duration) {
    Agreement a;
    size_t same = 0, steps = 0;
    for (double t = 0; t < duration; t += 0.1, ++steps) same += chord_at(ours, t) == chord_at(theirs, t);
    a.chords = steps ? static_cast<double>(same) / steps : 1.0;
    size_t matched = 0;
    for (const auto& bar : theirs) {
        for (const auto& o : ours)
            if (std::abs(o["start"].num() - bar["start"].num()) <= 0.025) {
                ++matched;
                break;
            }
    }
    a.bars = theirs.empty() ? 1.0 : static_cast<double>(matched) / theirs.size();
    a.identical = json::dump(ours) == json::dump(theirs);
    return a;
}

}  // namespace

int run(int argc, char** argv);

int main(int argc, char** argv) {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    try {
        return run(argc, argv);
    } catch (const std::exception& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 3;
    }
}

int run(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: golden_test <assets dir> <golden dir> [song ...] [--xnnpack] [--threads N]\n");
        return 2;
    }
    const std::string assets = argv[1];
    const fs::path golden = argv[2];
    std::vector<std::string> only;
    bool xnnpack = false;
    int threads = 4;
    for (int i = 3; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--xnnpack") xnnpack = true;
        else if (a == "--threads" && i + 1 < argc) threads = std::stoi(argv[++i]);
        else only.push_back(a);
    }

    const Tables tables = Tables::load(assets);
    const OnnxModel bt(assets + "/beat_this_small0.onnx", {threads, xnnpack, false});
    const OnnxModel chord_model(assets + "/chord_features.onnx", {2, false, false});
    const OnnxModel key_model(assets + "/key.onnx", {2, false, false});
    const Analyzer analyzer(AnalyzerConfig{assets, threads, xnnpack, false});
    auto parallel = [](std::vector<std::function<void()>>& tasks) {
        for (auto& t : tasks) t();
    };

    std::vector<fs::path> songs;
    for (const auto& entry : fs::directory_iterator(golden))
        if (entry.is_directory() && fs::exists(entry.path() / "stages.json") &&
            (only.empty() || std::find(only.begin(), only.end(), entry.path().filename().string()) != only.end()))
            songs.push_back(entry.path());
    std::sort(songs.begin(), songs.end());

    double sum_chords = 0, sum_bars = 0;
    int identical_songs = 0, analysed = 0;
    std::vector<std::string> summary;
    for (const auto& dir : songs) {
        const std::string name = dir.filename().string();
        auto p = [&](const char* file) { return (dir / file).string(); };
        std::printf("%s\n", name.c_str());
        const json::Value stages = json::parse_file(p("stages.json"));
        const auto pcm = load_npy<int16_t>(p("pcm.npy"));

        // chords: spectrogram, CNN, CRF
        const auto chord_spec_ref = load_npy<float>(p("chord_spec.npy"));
        const auto chord_spec = log_filtered_spectrogram(pcm.data.data(), pcm.data.size(), tables.chord, tables.chord_window);
        const double spec_rel = max_abs(chord_spec.data, chord_spec_ref.data) / max_abs_value(chord_spec_ref.data);
        check(chord_spec.shape == chord_spec_ref.shape && spec_rel < 1e-5, name, "chord spectrogram", fmt("%.1e rel", spec_rel));

        const auto features_ref = load_npy<float>(p("chord_features.npy"));
        auto feat_out = chord_model.run(chord_spec_ref.data.data(), {1, (int64_t)chord_spec_ref.rows(), (int64_t)chord_spec_ref.cols()});
        const double feat_rel = max_abs(feat_out[0].data, features_ref.data) / max_abs_value(features_ref.data);
        check(feat_rel < 1e-5, name, "chord CNN (ONNX)", fmt("%.1e rel", feat_rel));

        const auto crf_ref = load_npy<uint32_t>(p("crf_path.npy"));
        const auto crf_path = crf_decode(features_ref, tables);
        check(crf_path == crf_ref.data, name, "CRF path (on Python's features)",
              crf_path == crf_ref.data ? "identical" : "differs");
        const auto segments = chord_segments(crf_path, tables.crf_fps);
        bool seg_same = segments.size() == stages["segments"].list().size();
        for (size_t i = 0; seg_same && i < segments.size(); ++i) {
            const auto& s = stages["segments"][i];
            seg_same = segments[i].label == s[2].str() && segments[i].start == s[0].num() && segments[i].end == s[1].num();
        }
        check(seg_same, name, "chord segments", std::to_string(segments.size()) + " segments");

        // key
        const auto key_spec_ref = load_npy<float>(p("key_spec.npy"));
        const auto key_spec = log_filtered_spectrogram(pcm.data.data(), pcm.data.size(), tables.key, tables.chord_window);
        const double key_spec_rel = max_abs(key_spec.data, key_spec_ref.data) / max_abs_value(key_spec_ref.data);
        check(key_spec.shape == key_spec_ref.shape && key_spec_rel < 1e-5, name, "key spectrogram", fmt("%.1e rel", key_spec_rel));
        auto key_out = key_model.run(key_spec_ref.data.data(), {1, (int64_t)key_spec_ref.rows(), (int64_t)key_spec_ref.cols()});
        const auto key = key_from_probabilities(key_out[0].data);
        check(key.tonic + " " + key.mode == stages["key"].str(), name, "key", key.tonic + " " + key.mode);

        // beats: resampling, mel, Beat This!, DBN
        const auto resampled_ref = load_npy<float>(p("bt_resampled.npy"));
        const auto resampled = resample_int16(pcm.data.data(), pcm.data.size(), 44100.0, tables.bt_sample_rate);
        const double rs = max_abs(resampled, resampled_ref.data);
        check(resampled.size() == resampled_ref.data.size() && rs < 1e-5, name, "resampling (soxr)", fmt("%.1e abs", rs));

        const auto mel_ref = load_npy<float>(p("bt_mel.npy"));
        const auto mel = log_mel(resampled_ref.data.data(), resampled_ref.data.size(), tables);
        const double mel_rel = max_abs(mel.data, mel_ref.data) / max_abs_value(mel_ref.data);
        // 5e-5: numpy multiplies by the mel filterbank with BLAS (sgemm), which adds in another
        // order; the logits and everything after them still come out identical.
        check(mel.shape == mel_ref.shape && mel_rel < 5e-5, name, "log-mel", fmt("%.1e rel", mel_rel));

        const auto beat_ref = load_npy<float>(p("bt_beat_logits.npy"));
        const auto down_ref = load_npy<float>(p("bt_down_logits.npy"));
        const auto t0 = std::chrono::steady_clock::now();
        const auto logits = beat_this_logits(mel_ref, bt, tables);
        const double bt_seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        const double lg = std::max(max_abs(logits.beat, beat_ref.data), max_abs(logits.down, down_ref.data));
        check(lg < 1e-3, name, xnnpack ? "Beat This! logits (XNNPACK)" : "Beat This! logits", fmt("%.1e abs", lg) + fmt(", %.1f s", bt_seconds));

        const auto act_ref = load_npy<double>(p("dbn_activations.npy"));
        const auto act = dbn_activations(beat_ref.data, down_ref.data);
        std::vector<double> act_flat;
        for (size_t i = 0; i < act.size(); ++i) {
            act_flat.push_back(act.beat[i]);
            act_flat.push_back(act.down[i]);
        }
        const double ac = max_abs(act_flat, act_ref.data);
        check(ac < 1e-12, name, "DBN activations", fmt("%.1e abs", ac));

        Activations golden_act;
        for (size_t i = 0; i < act_ref.rows(); ++i) {
            golden_act.beat.push_back(act_ref.at(i, 0));
            golden_act.down.push_back(act_ref.at(i, 1));
        }
        const size_t first = static_cast<size_t>(stages["dbn_first"].num());
        for (const auto& hmm : tables.hmms) {
            const std::string m = std::to_string(hmm.beats_per_bar);
            const auto path_ref = load_npy<uint32_t>(p(("dbn" + m + "_path.npy").c_str()));
            const auto result = viterbi(hmm, golden_act, first, path_ref.data.size(), tables.observation_lambda);
            const double lp_ref = stages["dbn_log_probabilities"][m].num();
            const bool same = result.path == path_ref.data && result.log_probability == lp_ref;
            check(same, name, "Viterbi " + m + "/4 (low memory)",
                  (same ? std::string("identical") : "differs") + fmt(", %.1f MB", (result.checkpoint_bytes + result.segment_bytes) / 1048576.0));
        }
        const auto tracked = track_beats(golden_act, tables, parallel);
        const auto tracked_ref = load_npy<double>(p("tracked.npy"));
        bool tr_same = tracked.times.size() == tracked_ref.rows();
        for (size_t i = 0; tr_same && i < tracked.times.size(); ++i)
            tr_same = tracked.times[i] == tracked_ref.at(i, 0) && tracked.positions[i] == static_cast<int>(tracked_ref.at(i, 1));
        check(tr_same, name, "tracked beats", std::to_string(tracked.times.size()) + " beats");

        // the chart, from Python's beats, segments and key
        Tracked tracked_py;
        for (size_t i = 0; i < tracked_ref.rows(); ++i) {
            tracked_py.times.push_back(tracked_ref.at(i, 0));
            tracked_py.positions.push_back(static_cast<int>(tracked_ref.at(i, 1)));
        }
        std::vector<Segment> segments_py;
        for (const auto& s : stages["segments"].list()) segments_py.push_back({s[0].num(), s[1].num(), s[2].str()});
        const auto& song_ref = stages["song"];
        const Key key_py{song_ref["key"]["tonic"].str(), song_ref["key"]["mode"].str(), song_ref["key"]["confidence"].num()};
        const double duration = static_cast<double>(pcm.data.size()) / 44100.0;
        const Song chart = build_chart(tracked_py, segments_py, key_py, duration);
        const auto chart_bars = bars_json(chart);
        const bool chart_same = json::dump(chart_bars) == json::dump(song_ref["bars"]) && chart.bpm == song_ref["bpm"].num() &&
                                chart.meter == static_cast<int>(song_ref["meter"].num()) && chart.duration == song_ref["duration"].num();
        check(chart_same, name, "chart (bars, bpm, meter)", std::to_string(chart.bars.size()) + " bars");

        // end to end, from the PCM
        const Song song = analyzer.analyze(pcm.data.data(), pcm.data.size());
        const auto ag = agreement(bars_json(song), song_ref["bars"].list(), duration);
        const bool key_same = song.key.tonic == song_ref["key"]["tonic"].str() && song.key.mode == song_ref["key"]["mode"].str();
        std::printf("  end to end: %s, chords %.1f%%, bar lines %.1f%%, key %s, %.1f s\n", ag.identical ? "IDENTICAL" : "differs",
                    100 * ag.chords, 100 * ag.bars, key_same ? "same" : "DIFFERENT", song.elapsed);
        sum_chords += ag.chords;
        sum_bars += ag.bars;
        identical_songs += ag.identical;
        ++analysed;
        summary.push_back(name + fmt(": chords %.1f%%", 100 * ag.chords) + fmt(", bars %.1f%%", 100 * ag.bars) +
                          (ag.identical ? " (identical)" : ""));
    }
    std::printf("\n%d songs; end to end identical: %d; mean agreement: chords %.2f%%, bar lines %.2f%%\n", analysed,
                identical_songs, analysed ? 100 * sum_chords / analysed : 0, analysed ? 100 * sum_bars / analysed : 0);
    std::printf("%d stage check(s) failed\n", failures);
    return failures ? 1 : 0;
}
