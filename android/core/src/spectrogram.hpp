// Spectrograms, as the Python pipeline computes them:
//
// - madmom's LogarithmicFilteredSpectrogram (chords at 10 fps, key at 5 fps): frames of
//   8192 samples centred on i * hop (zero-padded at the edges), times np.hanning / 32767,
//   FFT in float64 (scipy.fftpack), cast to complex64, magnitude, filterbank, log10(1 + x);
// - Beat This!'s log-mel spectrogram (chordchart/beat_this.py log_mel): 22.05 kHz audio,
//   reflect-padded, Hann-windowed 1024-point frames every 441 samples, float32 rfft
//   (numpy 2 computes float32 input in float32), |X| / 32, mel filterbank, log1p(1000 x).

#pragma once

#include <cstdint>
#include <vector>

#include "npy.hpp"
#include "tables.hpp"

namespace chordchart {

// (frames x bands) float32
Array<float> log_filtered_spectrogram(const int16_t* pcm, size_t samples, const SpectrogramSpec& spec,
                                      const std::vector<double>& window);

// (frames x n_mels) float32, from audio already at Tables::bt_sample_rate
Array<float> log_mel(const float* audio, size_t samples, const Tables& tables);

}  // namespace chordchart
