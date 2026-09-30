#include "beat_this.hpp"

#include <algorithm>
#include <stdexcept>

#include "chordchart/analyzer.hpp"

namespace chordchart {

Logits beat_this_logits(const Array<float>& mel, const OnnxModel& model, const Tables& t,
                        const std::function<bool(double)>& on_chunk) {
    const long long n = static_cast<long long>(mel.rows());
    const long long chunk = t.bt_chunk, border = t.bt_border;
    const size_t n_mels = mel.cols();
    Logits out;
    out.beat.assign(static_cast<size_t>(n), -1000.0f);
    out.down.assign(static_cast<size_t>(n), -1000.0f);

    // starts = range(-border, n - border, chunk - 2 * border); the last one moved so the
    // final chunk ends at the song's end
    std::vector<long long> starts;
    for (long long s = -border; s < n - border; s += chunk - 2 * border) starts.push_back(s);
    if (n > chunk - 2 * border && !starts.empty()) starts.back() = n - (chunk - border);

    std::vector<float> buffer;
    size_t done = 0;
    for (auto it = starts.rbegin(); it != starts.rend(); ++it) {  // the first chunk wins overlaps
        const long long start = *it;
        const long long lo = std::max(start, 0LL), hi = std::min(start + chunk, n);
        const long long pad_front = std::max(0LL, -start);
        const long long pad_back = std::max(0LL, std::min(border, start + chunk - n));
        const long long frames = pad_front + (hi - lo) + pad_back;
        buffer.assign(static_cast<size_t>(frames) * n_mels, 0.0f);
        std::copy(mel.data.begin() + lo * static_cast<long long>(n_mels), mel.data.begin() + hi * static_cast<long long>(n_mels),
                  buffer.begin() + pad_front * static_cast<long long>(n_mels));
        auto result = model.run(buffer.data(), {1, frames, static_cast<int64_t>(n_mels)});
        const auto& b = result.at(0).data;
        const auto& d = result.at(1).data;
        // beat[start + border : start + chunk - border] = b[border:-border]  (numpy clips the
        // target slice at n; the lengths then agree)
        const long long target_lo = start + border;
        const long long target_hi = std::min(start + chunk - border, n);
        const long long source_len = frames - 2 * border;
        if (target_hi - target_lo != source_len) throw std::runtime_error("Beat This! chunk bookkeeping");
        for (long long i = 0; i < source_len; ++i) {
            out.beat[static_cast<size_t>(target_lo + i)] = b[static_cast<size_t>(border + i)];
            out.down[static_cast<size_t>(target_lo + i)] = d[static_cast<size_t>(border + i)];
        }
        if (on_chunk && !on_chunk(static_cast<double>(++done) / starts.size())) throw Cancelled();
    }
    return out;
}

}  // namespace chordchart
