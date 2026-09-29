#include "spectrogram.hpp"

#include <cmath>
#include <complex>
#include <stdexcept>

#include "pocketfft_hdronly.h"

namespace chordchart {

Array<float> log_filtered_spectrogram(const int16_t* pcm, size_t samples, const SpectrogramSpec& spec,
                                      const std::vector<double>& window) {
    const size_t n = spec.frame_size;
    const size_t bins = n / 2;  // madmom drops the Nyquist bin
    if (window.size() != n) throw std::runtime_error("window size");
    // madmom FramedSignal, end='normal': ceil(len / hop) frames
    const size_t frames = static_cast<size_t>(std::ceil(static_cast<double>(samples) / spec.hop_size));

    Array<float> out;
    out.shape = {frames, spec.filterbank.bands};
    out.data.assign(frames * spec.filterbank.bands, 0.0f);

    std::vector<double> frame(n);
    std::vector<std::complex<double>> fft(n / 2 + 1);
    std::vector<float> magnitude(bins);
    const pocketfft::shape_t shape{n};
    const pocketfft::stride_t stride_in{sizeof(double)};
    const pocketfft::stride_t stride_out{sizeof(std::complex<double>)};

    for (size_t f = 0; f < frames; ++f) {
        // signal_frame: reference sample int(f * hop), window centred on it
        const long long ref = static_cast<long long>(static_cast<double>(f) * spec.hop_size);
        const long long start = ref - static_cast<long long>(n / 2);
        for (size_t i = 0; i < n; ++i) {
            const long long s = start + static_cast<long long>(i);
            const double sample = (s >= 0 && s < static_cast<long long>(samples)) ? pcm[s] : 0.0;
            frame[i] = sample * window[i];  // np.multiply(int16 frame, float64 window)
        }
        pocketfft::r2c(shape, stride_in, stride_out, 0, pocketfft::FORWARD, frame.data(), fft.data(), 1.0);
        for (size_t b = 0; b < bins; ++b) {
            // STFT_DTYPE complex64, then np.abs in float32
            const std::complex<float> c(static_cast<float>(fft[b].real()), static_cast<float>(fft[b].imag()));
            magnitude[b] = std::hypot(c.real(), c.imag());
        }
        float* row = &out.data[f * spec.filterbank.bands];
        for (size_t band = 0; band < spec.filterbank.bands; ++band) {
            float acc = 0.0f;
            for (const auto& [bin, weight] : spec.filterbank.by_band[band]) acc += magnitude[bin] * weight;
            row[band] = std::log10(acc * spec.log_mul + spec.log_add);
        }
    }
    return out;
}

Array<float> log_mel(const float* audio, size_t samples, const Tables& t) {
    const size_t n_fft = static_cast<size_t>(t.bt_n_fft);
    const size_t hop = static_cast<size_t>(t.bt_hop);
    const size_t pad = n_fft / 2;
    const size_t n_mels = static_cast<size_t>(t.bt_n_mels);
    if (samples <= pad) throw std::runtime_error("audio too short for the beat tracker");

    // np.pad(x, n_fft // 2, mode="reflect")
    std::vector<float> x(samples + 2 * pad);
    for (size_t i = 0; i < pad; ++i) x[i] = audio[pad - i];
    std::copy(audio, audio + samples, x.begin() + static_cast<long long>(pad));
    for (size_t i = 0; i < pad; ++i) x[pad + samples + i] = audio[samples - 2 - i];

    const size_t frames = 1 + (x.size() - n_fft) / hop;
    const size_t bins = n_fft / 2 + 1;
    Array<float> out;
    out.shape = {frames, n_mels};
    out.data.assign(frames * n_mels, 0.0f);

    std::vector<float> frame(n_fft);
    std::vector<std::complex<float>> fft(bins);
    std::vector<float> spec(bins);
    const pocketfft::shape_t shape{n_fft};
    const pocketfft::stride_t stride_in{sizeof(float)};
    const pocketfft::stride_t stride_out{sizeof(std::complex<float>)};
    const float scale = std::sqrt(static_cast<float>(n_fft));
    const auto& fb = t.bt_mel_filterbank;  // bins x n_mels
    for (size_t f = 0; f < frames; ++f) {
        const float* src = &x[f * hop];
        for (size_t i = 0; i < n_fft; ++i) frame[i] = src[i] * t.bt_window[i];
        pocketfft::r2c(shape, stride_in, stride_out, 0, pocketfft::FORWARD, frame.data(), fft.data(), 1.0f);
        for (size_t b = 0; b < bins; ++b) spec[b] = std::abs(fft[b]) / scale;
        float* row = &out.data[f * n_mels];
        for (size_t b = 0; b < bins; ++b) {
            const float v = spec[b];
            if (v == 0.0f) continue;
            const float* weights = &fb.data[b * n_mels];
            for (size_t m = 0; m < n_mels; ++m) row[m] += v * weights[m];
        }
        for (size_t m = 0; m < n_mels; ++m) row[m] = std::log1p(1000.0f * row[m]);
    }
    return out;
}

}  // namespace chordchart
