#include "dbn.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>

namespace chordchart {

Activations dbn_activations(const std::vector<float>& beat_logits, const std::vector<float>& down_logits) {
    // chordchart/beat_this.py dbn_activations, in float64 like it
    constexpr double epsilon = 1e-5;
    Activations a;
    const size_t n = beat_logits.size();
    a.beat.resize(n);
    a.down.resize(n);
    for (size_t i = 0; i < n; ++i) {
        double beat = 1.0 / (1.0 + std::exp(-static_cast<double>(beat_logits[i])));
        double down = 1.0 / (1.0 + std::exp(-static_cast<double>(down_logits[i])));
        beat = beat * (1 - epsilon) + epsilon / 2;
        down = down * (1 - epsilon) + epsilon / 2;
        a.beat[i] = std::max(beat - down, epsilon / 2);
        a.down[i] = down;
    }
    return a;
}

namespace {

constexpr double kNegInf = -std::numeric_limits<double>::infinity();

// RNNDownBeatTrackingObservationModel.log_densities, per frame: [no beat, beat, downbeat]
std::vector<double> log_densities(const Activations& a, size_t first, size_t count, double lambda) {
    std::vector<double> d(count * 3);
    for (size_t i = 0; i < count; ++i) {
        const double b = a.beat[first + i], dn = a.down[first + i];
        d[i * 3 + 0] = std::log((1.0 - (b + dn)) / (lambda - 1));
        d[i * 3 + 1] = std::log(b);
        d[i * 3 + 2] = std::log(dn);
    }
    return d;
}

// One Viterbi frame, in madmom's order: previous + transition + density, and a later
// predecessor wins only if strictly greater. `winner` (optional) gets each state's winning
// predecessor as an index within its list.
void step(const BarHmm& hmm, const double* density, const std::vector<double>& previous, std::vector<double>& current,
          uint8_t* winner) {
    const uint32_t* ptr = hmm.pointers.data();
    for (size_t s = 0; s < hmm.num_states; ++s) {
        const double dens = density[hmm.om_pointers[s]];
        double best = kNegInf;
        uint8_t best_index = 0;
        for (uint32_t p = ptr[s]; p < ptr[s + 1]; ++p) {
            const double v = previous[hmm.states[p]] + hmm.log_probs[p] + dens;
            if (v > best) {
                best = v;
                best_index = static_cast<uint8_t>(p - ptr[s]);
            }
        }
        current[s] = best;
        if (winner) winner[s] = best_index;
    }
}

}  // namespace

ViterbiResult viterbi(const BarHmm& hmm, const Activations& activations, size_t first, size_t count,
                      double observation_lambda, size_t checkpoint_every) {
    for (size_t s = 0; s < hmm.num_states; ++s)
        if (hmm.pointers[s + 1] - hmm.pointers[s] > 255) throw std::runtime_error("more than 255 predecessors");
    const std::vector<double> densities = log_densities(activations, first, count, observation_lambda);
    const size_t k = checkpoint_every ? checkpoint_every
                                      : std::max<size_t>(1, static_cast<size_t>(std::sqrt(static_cast<double>(count))));
    ViterbiResult result;

    std::vector<std::vector<double>> checkpoints;
    std::vector<double> previous = hmm.log_initial, current(hmm.num_states);
    for (size_t frame = 0; frame < count; ++frame) {
        if (frame % k == 0) checkpoints.push_back(previous);
        step(hmm, &densities[frame * 3], previous, current, nullptr);
        previous.swap(current);
    }
    result.checkpoint_bytes = checkpoints.size() * hmm.num_states * sizeof(double);

    // the final best state (numpy argmax: the first maximum)
    size_t state = 0;
    for (size_t s = 1; s < hmm.num_states; ++s)
        if (previous[s] > previous[state]) state = s;
    result.log_probability = count ? previous[state] : kNegInf;
    if (count == 0 || std::isinf(result.log_probability)) return result;

    result.path.resize(count);
    std::vector<uint8_t> winners;
    for (size_t segment = checkpoints.size(); segment-- > 0;) {
        const size_t begin = segment * k, end = std::min(count, (segment + 1) * k);
        winners.assign((end - begin) * hmm.num_states, 0);
        result.segment_bytes = std::max(result.segment_bytes, winners.size());
        std::vector<double> scores = checkpoints[segment];
        for (size_t frame = begin; frame < end; ++frame) {
            step(hmm, &densities[frame * 3], scores, current, &winners[(frame - begin) * hmm.num_states]);
            scores.swap(current);
        }
        checkpoints[segment].clear();
        checkpoints[segment].shrink_to_fit();
        for (size_t frame = end; frame-- > begin;) {
            result.path[frame] = static_cast<uint32_t>(state);
            state = hmm.states[hmm.pointers[state] + winners[(frame - begin) * hmm.num_states + state]];
        }
    }
    return result;
}

Tracked track_beats(const Activations& a, const Tables& t, const Parallel& parallel) {
    Tracked tracked;
    // madmom threshold_activations: rows with any activation >= threshold
    size_t first = 0, last = 0;
    {
        bool any_nonzero_index = false;
        size_t lo = std::numeric_limits<size_t>::max(), hi = 0;
        for (size_t i = 0; i < a.size(); ++i) {
            if (a.beat[i] >= t.dbn_threshold || a.down[i] >= t.dbn_threshold) {
                lo = std::min(lo, i);
                hi = std::max(hi, i);
                if (i != 0) any_nonzero_index = true;
            }
        }
        if (any_nonzero_index) {  // numpy `idx.any()`: False if only frame 0 qualifies
            first = lo;
            last = std::min(a.size(), hi + 1);
        }
    }
    const size_t count = last - first;
    if (count == 0) return tracked;  // `not activations.any()`

    std::vector<ViterbiResult> results(t.hmms.size());
    std::vector<std::function<void()>> tasks;
    for (size_t m = 0; m < t.hmms.size(); ++m)
        tasks.emplace_back([&, m] { results[m] = viterbi(t.hmms[m], a, first, count, t.observation_lambda); });
    parallel(tasks);

    size_t best = 0;  // np.argmax of the log-probabilities: the first maximum
    for (size_t m = 1; m < results.size(); ++m)
        if (results[m].log_probability > results[best].log_probability) best = m;
    const auto& path = results[best].path;
    const BarHmm& hmm = t.hmms[best];
    if (path.empty()) return tracked;

    std::vector<int> beat_numbers(count);
    for (size_t i = 0; i < count; ++i) beat_numbers[i] = static_cast<int>(hmm.positions[path[i]]) + 1;

    std::vector<size_t> beats;
    if (t.dbn_correct) {
        std::vector<bool> in_range(count);
        bool any = false;
        for (size_t i = 0; i < count; ++i) any |= (in_range[i] = hmm.om_pointers[path[i]] >= 1);
        if (!any) return tracked;
        std::vector<size_t> idx;
        if (in_range[0]) idx.push_back(0);
        for (size_t i = 0; i + 1 < count; ++i)
            if (in_range[i] != in_range[i + 1]) idx.push_back(i + 1);
        if (in_range[count - 1]) idx.push_back(count);
        for (size_t j = 0; j + 1 < idx.size(); j += 2) {
            const size_t left = idx[j], right = idx[j + 1];
            // np.argmax over activations[left:right] flattened (row-major), // 2
            size_t best_flat = 0;
            double best_value = -std::numeric_limits<double>::infinity();
            for (size_t r = left; r < right; ++r) {
                const double values[2] = {a.beat[first + r], a.down[first + r]};
                for (size_t c = 0; c < 2; ++c) {
                    const size_t flat = (r - left) * 2 + c;
                    if (values[c] > best_value) {
                        best_value = values[c];
                        best_flat = flat;
                    }
                }
            }
            beats.push_back(best_flat / 2 + left);
        }
    } else {
        for (size_t i = 0; i + 1 < count; ++i)
            if (beat_numbers[i + 1] != beat_numbers[i]) beats.push_back(i + 1);
    }
    for (size_t b : beats) {
        tracked.times.push_back(static_cast<double>(b + first) / t.dbn_fps);
        tracked.positions.push_back(beat_numbers[b]);
    }
    return tracked;
}

}  // namespace chordchart
