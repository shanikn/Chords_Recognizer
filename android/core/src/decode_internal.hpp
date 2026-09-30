// The in-process decoders for MP3 and AAC (decode_mpeg.cpp), for decode_file.

#pragma once

#include <cstdint>
#include <functional>
#include <vector>

#include "chordchart/demux.hpp"

namespace chordchart {

// Both throw UnsupportedContainer wherever Android's decoder would fail or the app's
// decoding loop would behave differently (e.g. the format changing mid-stream): the app then
// decodes the file with the platform's decoder, as before.
std::vector<int16_t> decode_mp3(Demuxer& demuxer, const std::function<void(double)>& progress);
std::vector<int16_t> decode_aac(Demuxer& demuxer, const std::function<void(double)>& progress);

// GetMPEGAudioFrameSize (AOSP media/module/foundation/avc_utils.cpp), in demux_mpeg.cpp.
bool mpeg_audio_frame_size(uint32_t header, size_t* frame_size, int* out_sampling_rate = nullptr,
                           int* out_channels = nullptr, int* out_bitrate = nullptr, int* out_num_samples = nullptr);

}  // namespace chordchart
