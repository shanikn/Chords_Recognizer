#include "crf.hpp"

namespace chordchart {

std::vector<uint32_t> crf_decode(const Array<float>& features, const Tables& t) {
    const size_t n = features.rows();
    const size_t classes = t.crf_pi.data.size();
    const size_t dims = t.crf_W.rows();
    std::vector<uint32_t> path(n);
    if (n == 0) return path;
    std::vector<uint32_t> back(n * classes);
    std::vector<float> viterbi = t.crf_pi.data, next(classes), best(classes);
    for (size_t i = 0; i < n; ++i) {
        // all_trans = A + viterbi[:, None]; best over the previous class (first max)
        for (size_t j = 0; j < classes; ++j) {
            size_t arg = 0;
            float value = t.crf_A.at(0, j) + viterbi[0];
            for (size_t p = 1; p < classes; ++p) {
                const float v = t.crf_A.at(p, j) + viterbi[p];
                if (v > value) {
                    value = v;
                    arg = p;
                }
            }
            best[j] = value;
            back[i * classes + j] = static_cast<uint32_t>(arg);
        }
        // viterbi = c + dot(observations[i], W) + best_trans
        for (size_t j = 0; j < classes; ++j) {
            double dot = 0;
            for (size_t k = 0; k < dims; ++k)
                dot += static_cast<double>(features.at(i, k)) * static_cast<double>(t.crf_W.at(k, j));
            next[j] = (t.crf_c.data[j] + static_cast<float>(dot)) + best[j];
        }
        viterbi.swap(next);
    }
    for (size_t j = 0; j < classes; ++j) viterbi[j] += t.crf_tau.data[j];
    size_t state = 0;
    for (size_t j = 1; j < classes; ++j)
        if (viterbi[j] > viterbi[state]) state = j;
    path[n - 1] = static_cast<uint32_t>(state);
    for (size_t i = n - 1; i-- > 0;) path[i] = back[(i + 1) * classes + path[i + 1]];
    return path;
}

std::vector<Segment> chord_segments(const std::vector<uint32_t>& path, double fps) {
    static const char* roots[] = {"A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"};
    auto label = [](uint32_t p) -> std::string {
        if (p == 24) return "N";
        return std::string(roots[p % 12]) + (p < 12 ? ":maj" : ":min");
    };
    const double spf = 1.0 / fps;
    std::vector<Segment> segments;
    for (size_t i = 0; i < path.size(); ++i) {
        std::string l = label(path[i]);
        if (segments.empty() || segments.back().label != l) {
            if (!segments.empty()) segments.back().end = static_cast<double>(i) * spf;
            segments.push_back({static_cast<double>(i) * spf, 0, l});
        }
    }
    if (!segments.empty()) segments.back().end = static_cast<double>(path.size() - 1) * spf + spf;
    return segments;
}

}  // namespace chordchart
