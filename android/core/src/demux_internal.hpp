// Container readers in demux_mpeg.cpp, for Demuxer::open.

#pragma once

#include "chordchart/demux.hpp"

namespace chordchart {

// nullptr if `data` doesn't start like an MP4 file (then `data` is untouched); throws
// UnsupportedContainer if it's an MP4 the core doesn't read (no AAC, fragmented, ...).
std::unique_ptr<Demuxer> open_mp4(std::vector<uint8_t>& data);
// Throws UnsupportedContainer if no MP3 stream is found.
std::unique_ptr<Demuxer> open_mp3(std::vector<uint8_t>& data);

}  // namespace chordchart
