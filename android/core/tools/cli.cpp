// Analyse 16-bit mono 44.1 kHz PCM from a .npy file and print the Song as JSON.
//
//   chordchart_cli <assets dir> <pcm.npy> [--xnnpack] [--threads N] [--arena]

#include <cstdio>
#include <string>

#include "chordchart/analyzer.hpp"
#include "npy.hpp"

int main(int argc, char** argv) {
    if (argc < 3) {
        std::fprintf(stderr, "usage: chordchart_cli <assets dir> <pcm.npy> [--xnnpack] [--threads N] [--arena]\n");
        return 2;
    }
    chordchart::AnalyzerConfig config;
    config.assets_dir = argv[1];
    for (int i = 3; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--xnnpack") config.xnnpack = true;
        else if (a == "--arena") config.ort_arena = true;
        else if (a == "--threads" && i + 1 < argc) config.threads = std::stoi(argv[++i]);
    }
    try {
        const auto pcm = chordchart::load_npy<int16_t>(argv[2]);
        chordchart::Analyzer analyzer(config);
        auto song = analyzer.analyze(pcm.data.data(), pcm.data.size(), [](const std::string& stage, double f) {
            std::fprintf(stderr, "%3.0f%% %s\n", 100 * f, stage.c_str());
        });
        std::printf("%s\n", song.to_json().c_str());
    } catch (const chordchart::AnalysisError& e) {
        std::fprintf(stderr, "error: %s\n", e.what());
        return 1;
    } catch (const std::exception& e) {
        std::fprintf(stderr, "internal error: %s\n", e.what());
        return 3;
    }
    return 0;
}
