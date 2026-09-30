// Decoded audio -> what the analysis takes: mono, 44.1 kHz, 16-bit.
//
// The desktop pipeline gets that from ffmpeg (-ac 1 -ar 44100 pcm_s16le). A phone's
// decoder gives the file's own rate and channels instead; this mixes them down (the mean
// of the channels, as ffmpeg's default mono downmix does for stereo) and resamples with
// libsoxr (HQ). ffmpeg resamples with its own filter, so for files not at 44.1 kHz the
// samples differ slightly from the desktop's (the analysis is robust to that; the
// end-to-end comparison measures it).

#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

namespace chordchart {

class MonoResampler {
public:
    MonoResampler(int sample_rate, int channels);
    ~MonoResampler();
    MonoResampler(const MonoResampler&) = delete;
    MonoResampler& operator=(const MonoResampler&) = delete;

    // Interleaved 16-bit samples (all channels), fed as the decoder produces them.
    void feed(const int16_t* interleaved, size_t samples);
    // Interleaved float samples (decoders that output float PCM), in [-1, 1].
    void feed_float(const float* interleaved, size_t samples);
    // Flushes the resampler and returns the whole song as mono 44.1 kHz int16.
    std::vector<int16_t> finish();

private:
    void push(const float* mono, size_t frames);
    int rate_, channels_;
    void* soxr_ = nullptr;
    std::vector<float> mono_, out_;
    std::vector<int16_t> result_;
};

}  // namespace chordchart
