// Resampling with libsoxr exactly as python-soxr 1.1.0's soxr.resample(x, in, out) does it
// (csoxr_split_ch): quality HQ, float32, input fed in chunks of max(1000, 48000 * in / out)
// samples, output buffer ilen * out / in + 1, then a flush. The chunks change the output's
// rounding, so they're reproduced too. The int16 input is converted chunk by chunk
// (x / 32768, as chordchart.beats._samples does), so no full float copy of the song is made.

#pragma once

#include <cstdint>
#include <vector>

namespace chordchart {

std::vector<float> resample_int16(const int16_t* pcm, size_t samples, double in_rate, double out_rate);

}  // namespace chordchart
