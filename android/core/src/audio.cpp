#include "chordchart/audio.hpp"

#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <string>

#include "soxr.h"

namespace chordchart {

namespace {

constexpr double kTargetRate = 44100.0;

int16_t to_int16(float v) {
    // float -> s16 as ffmpeg's swresample does it: scale by 32768, round to nearest, clip
    const float scaled = v * 32768.0f;
    const long r = std::lrintf(scaled);
    return static_cast<int16_t>(std::clamp<long>(r, -32768, 32767));
}

}  // namespace

MonoResampler::MonoResampler(int sample_rate, int channels) : rate_(sample_rate), channels_(channels) {
    if (sample_rate <= 0 || channels <= 0) throw std::runtime_error("bad audio format");
    if (sample_rate != static_cast<int>(kTargetRate)) {
        const soxr_io_spec_t io = soxr_io_spec(SOXR_FLOAT32_I, SOXR_FLOAT32_I);
        const soxr_quality_spec_t quality = soxr_quality_spec(SOXR_HQ, 0);
        soxr_error_t err = nullptr;
        soxr_ = soxr_create(sample_rate, kTargetRate, 1, &err, &io, &quality, nullptr);
        if (err) throw std::runtime_error(std::string("soxr: ") + err);
    }
}

MonoResampler::~MonoResampler() {
    if (soxr_) soxr_delete(static_cast<soxr_t>(soxr_));
}

void MonoResampler::feed(const int16_t* interleaved, size_t samples) {
    const size_t frames = samples / static_cast<size_t>(channels_);
    mono_.resize(frames);
    for (size_t f = 0; f < frames; ++f) {
        float sum = 0;
        for (int c = 0; c < channels_; ++c) sum += interleaved[f * channels_ + c] / 32768.0f;
        mono_[f] = sum / static_cast<float>(channels_);
    }
    push(mono_.data(), frames);
}

void MonoResampler::feed_float(const float* interleaved, size_t samples) {
    const size_t frames = samples / static_cast<size_t>(channels_);
    mono_.resize(frames);
    for (size_t f = 0; f < frames; ++f) {
        float sum = 0;
        for (int c = 0; c < channels_; ++c) sum += interleaved[f * channels_ + c];
        mono_[f] = sum / static_cast<float>(channels_);
    }
    push(mono_.data(), frames);
}

void MonoResampler::push(const float* mono, size_t frames) {
    if (!soxr_) {
        for (size_t i = 0; i < frames; ++i) result_.push_back(to_int16(mono[i]));
        return;
    }
    out_.resize(static_cast<size_t>(std::ceil(frames * kTargetRate / rate_)) + 64);
    size_t done = 0;
    soxr_error_t err = soxr_process(static_cast<soxr_t>(soxr_), mono, frames, nullptr, out_.data(), out_.size(), &done);
    if (err) throw std::runtime_error(std::string("soxr: ") + err);
    for (size_t i = 0; i < done; ++i) result_.push_back(to_int16(out_[i]));
}

std::vector<int16_t> MonoResampler::finish() {
    if (soxr_) {
        out_.resize(4096);
        size_t done = 0;
        do {
            soxr_error_t err = soxr_process(static_cast<soxr_t>(soxr_), nullptr, 0, nullptr, out_.data(), out_.size(), &done);
            if (err) throw std::runtime_error(std::string("soxr: ") + err);
            for (size_t i = 0; i < done; ++i) result_.push_back(to_int16(out_[i]));
        } while (done > 0);
    }
    return std::move(result_);
}

}  // namespace chordchart
