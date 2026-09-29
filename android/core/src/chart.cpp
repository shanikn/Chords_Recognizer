#include "chart.hpp"

#include <algorithm>
#include <map>
#include <stdexcept>

#include "json.hpp"

namespace chordchart {

double bpm_from_times(const std::vector<double>& times) {
    if (times.size() < 2) return 0.0;
    std::vector<double> diffs(times.size() - 1);
    for (size_t i = 0; i + 1 < times.size(); ++i) diffs[i] = times[i + 1] - times[i];
    std::sort(diffs.begin(), diffs.end());
    const size_t n = diffs.size();
    const double median = n % 2 ? diffs[n / 2] : (diffs[n / 2 - 1] + diffs[n / 2]) / 2.0;
    return 60.0 / median;
}

int meter_from_positions(const std::vector<int>& positions) {
    std::vector<size_t> downbeats;
    for (size_t i = 0; i < positions.size(); ++i)
        if (positions[i] == 1) downbeats.push_back(i);
    if (downbeats.size() < 2) {
        if (positions.empty()) return 4;
        return *std::max_element(positions.begin(), positions.end());
    }
    // Counter(lengths).most_common(1): the most frequent; on a tie, the first seen
    std::vector<std::pair<size_t, size_t>> counts;  // (length, count) in first-seen order
    for (size_t i = 0; i + 1 < downbeats.size(); ++i) {
        const size_t length = downbeats[i + 1] - downbeats[i];
        auto it = std::find_if(counts.begin(), counts.end(), [&](auto& c) { return c.first == length; });
        if (it == counts.end()) counts.emplace_back(length, 1);
        else ++it->second;
    }
    auto best = counts.begin();
    for (auto it = counts.begin(); it != counts.end(); ++it)
        if (it->second > best->second) best = it;
    return static_cast<int>(best->first);
}

std::vector<int> split_points(int b) {
    std::vector<int> points;
    if (b <= 2) {
        for (int i = 0; i < b; ++i) points.push_back(i);
    } else if (b == 3) {
        points = {0, 2};
    } else if (b % 3 == 0) {
        for (int i = 0; i < b; i += 3) points.push_back(i);
    } else if (b % 2 == 0) {
        for (int i = 0; i < b; i += 2) points.push_back(i);
    } else {
        points = {0};
    }
    return points;
}

std::string harte_to_symbol(const std::string& harte) {
    if (harte == "N" || harte == "X") return "N";
    auto colon = harte.find(':');
    std::string root = harte.substr(0, colon);
    std::string quality = colon == std::string::npos ? "maj" : harte.substr(colon + 1);
    if (quality == "maj") return root;
    if (quality == "min") return root + "m";
    throw std::runtime_error("unsupported chord quality in " + harte);
}

std::vector<std::string> beat_sync(const std::vector<Segment>& segments, const std::vector<double>& beat_times,
                                   double end_time) {
    std::vector<std::string> labels;
    for (size_t i = 0; i < beat_times.size(); ++i) {
        const double start = beat_times[i];
        const double end = i + 1 < beat_times.size() ? beat_times[i + 1] : end_time;
        std::vector<std::pair<std::string, double>> overlap;  // insertion order, like the dict
        for (const auto& seg : segments) {
            const double amount = std::min(end, seg.end) - std::max(start, seg.start);
            if (amount > 0) {
                auto it = std::find_if(overlap.begin(), overlap.end(), [&](auto& o) { return o.first == seg.label; });
                if (it == overlap.end()) overlap.emplace_back(seg.label, amount);
                else it->second += amount;
            }
        }
        if (overlap.empty()) {
            labels.push_back("N");
            continue;
        }
        auto best = overlap.begin();  // max(): the first maximum
        for (auto it = overlap.begin(); it != overlap.end(); ++it)
            if (it->second > best->second) best = it;
        labels.push_back(best->first);
    }
    return labels;
}

namespace {

std::vector<ChordEvent> quantize_bar(const std::vector<double>& times, const std::vector<int>& positions,
                                     const std::vector<std::string>& labels, const std::vector<int>& points) {
    std::map<int, std::vector<size_t>> regions;  // sorted by split point, like sorted(regions)
    for (size_t i = 0; i < positions.size(); ++i) {
        int region = -1;
        for (int p : points)
            if (p <= positions[i] - 1) region = std::max(region, p);
        if (region < 0) throw std::runtime_error("beat before the first split point");
        regions[region].push_back(i);
    }
    std::vector<ChordEvent> events;
    for (const auto& [point, idx] : regions) {
        std::vector<std::pair<std::string, size_t>> counts;  // first-seen order
        for (size_t i : idx) {
            auto it = std::find_if(counts.begin(), counts.end(), [&](auto& c) { return c.first == labels[i]; });
            if (it == counts.end()) counts.emplace_back(labels[i], 1);
            else ++it->second;
        }
        auto best = counts.begin();
        for (auto it = counts.begin(); it != counts.end(); ++it)
            if (it->second > best->second) best = it;
        const std::string& label = best->first;
        if (!events.empty() && events.back().harte == label) continue;
        const size_t first = idx.front();
        events.push_back({positions[first] - 1, times[first], harte_to_symbol(label), label});
    }
    return events;
}

}  // namespace

Song build_chart(const Tracked& tracked, const std::vector<Segment>& segments, const Key& key, double duration) {
    // Beats: bpm and meter from all tracked beats (before trimming, as the pipeline does)
    const double bpm = bpm_from_times(tracked.times);
    const int meter = meter_from_positions(tracked.positions);

    // whole song: the chart runs from 0 to the end of the audio (pipeline._bar_range)
    const double lo = 0.0, hi = duration;
    std::vector<double> times;
    std::vector<int> positions;
    for (size_t i = 0; i < tracked.times.size(); ++i) {
        const double t = tracked.times[i];
        if (lo - 1e-6 <= t && t < hi - 1e-6) {
            times.push_back(t);
            positions.push_back(tracked.positions[i]);
        }
    }
    std::vector<Segment> clipped;
    for (const auto& s : segments)
        if (s.end > lo && s.start < hi) clipped.push_back({std::max(s.start, lo), std::min(s.end, hi), s.label});
    if (std::count(positions.begin(), positions.end(), 1) < kMinBars)
        throw AnalysisError("could not find a steady beat (fewer than " + std::to_string(kMinBars) + " bars detected)");

    const auto labels = beat_sync(clipped, times, hi);

    Song song;
    song.duration = hi - lo;
    song.key = key;
    song.bpm = bpm;
    song.meter = meter;
    // group_bars
    const auto points = split_points(meter);
    std::vector<size_t> starts;
    for (size_t i = 0; i < positions.size(); ++i)
        if (positions[i] == 1) starts.push_back(i);
    int first_index = 1;
    if (starts.front() > 0) {
        starts.insert(starts.begin(), 0);
        first_index = 0;
    }
    starts.push_back(times.size());
    for (size_t n = 0; n + 1 < starts.size(); ++n) {
        const size_t a = starts[n], b = starts[n + 1];
        Bar bar;
        bar.index = first_index + static_cast<int>(n);
        bar.start = times[a];
        bar.end = b < times.size() ? times[b] : hi;
        bar.chords = quantize_bar(std::vector<double>(times.begin() + static_cast<long>(a), times.begin() + static_cast<long>(b)),
                                  std::vector<int>(positions.begin() + static_cast<long>(a), positions.begin() + static_cast<long>(b)),
                                  std::vector<std::string>(labels.begin() + static_cast<long>(a), labels.begin() + static_cast<long>(b)),
                                  points);
        song.bars.push_back(std::move(bar));
    }
    if (meter == 2) song.warnings.push_back("2 beats per bar detected: this may be 6/8 counted in dotted quarters.");
    return song;
}

Key key_from_probabilities(const std::vector<float>& p) {
    static const char* labels[] = {"A major", "Bb major", "B major",  "C major",  "Db major", "D major",
                                   "Eb major", "E major", "F major",  "F# major", "G major",  "Ab major",
                                   "A minor", "Bb minor", "B minor",  "C minor",  "C# minor", "D minor",
                                   "D# minor", "E minor", "F minor",  "F# minor", "G minor",  "G# minor"};
    if (p.size() != 24) throw std::runtime_error("key: expected 24 probabilities");
    size_t best = 0;
    for (size_t i = 1; i < p.size(); ++i)
        if (p[i] > p[best]) best = i;
    std::string label = labels[best];
    auto space = label.find(' ');
    return {label.substr(0, space), label.substr(space + 1), static_cast<double>(p[best])};
}

std::string Song::to_json() const {
    json::List bar_list;
    for (const auto& bar : bars) {
        json::List chords;
        for (const auto& c : bar.chords)
            chords.push_back(json::Object{{"beat", c.beat}, {"time", c.time}, {"symbol", c.symbol}, {"harte", c.harte}});
        bar_list.push_back(json::Object{{"index", bar.index}, {"start", bar.start}, {"end", bar.end}, {"chords", chords}});
    }
    json::List warning_list(warnings.begin(), warnings.end());
    json::Object timing_obj;
    for (const auto& [k, v] : timings) timing_obj[k] = v;
    json::Object song{
        {"duration", duration},
        {"key", json::Object{{"tonic", key.tonic}, {"mode", key.mode}, {"confidence", key.confidence}}},
        {"bpm", bpm},
        {"meter", meter},
        {"bars", bar_list},
        {"warnings", warning_list},
        {"section_start", 0.0},
        {"section_end", nullptr},
        {"requested_start", nullptr},
        {"requested_end", nullptr},
        {"timings", timing_obj},
        {"elapsed", elapsed},
    };
    return json::dump(song);
}

}  // namespace chordchart
