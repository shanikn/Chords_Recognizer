// Whole-file decoding in-process, for the formats the core can read and decode itself:
// WebM/Matroska and Ogg with Opus (YouTube audio). Result: mono 44.1 kHz int16, as
// MonoResampler gives it.
//
// Opus is decoded exactly as Android's own decoder does it (Codec2's C2SoftOpusDec): libopus
// 1.5 built fixed-point like AOSP's, multistream decode to int16 at 48 kHz, the header's
// gain, the codec delay discarded at the start and at every packet with timestamp 0. Doing it
// in-process avoids a round trip to the media process per 20 ms packet.

#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace chordchart {

// Throws UnsupportedContainer (demux.hpp) if the file isn't WebM/Ogg with Opus.
std::vector<int16_t> decode_file(const std::string& path, const std::function<void(double)>& progress = {});

}  // namespace chordchart
