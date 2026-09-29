// From beats, chord segments and key to the bar-by-bar chart, as the desktop pipeline does
// for a whole song: chordchart/beats.py (bpm, meter), pipeline._analyze (chart range, trim,
// the two-bar minimum, the 2/4 warning) and postprocess.py (beat_sync, group_bars,
// split_points, _quantize_bar), with Python's tie-breaking (the first-inserted label wins).

#pragma once

#include <string>
#include <vector>

#include "chordchart/analyzer.hpp"
#include "crf.hpp"
#include "dbn.hpp"

namespace chordchart {

constexpr int kMinBars = 2;

double bpm_from_times(const std::vector<double>& times);
int meter_from_positions(const std::vector<int>& positions);
std::vector<int> split_points(int beats_per_bar);
std::string harte_to_symbol(const std::string& harte);

std::vector<std::string> beat_sync(const std::vector<Segment>& segments, const std::vector<double>& beat_times,
                                   double end_time);

// Throws AnalysisError when fewer than kMinBars downbeats remain.
Song build_chart(const Tracked& tracked, const std::vector<Segment>& segments, const Key& key, double duration);

// madmom's KEY_LABELS index -> Key (confidence = the winning probability)
Key key_from_probabilities(const std::vector<float>& probabilities);

}  // namespace chordchart
