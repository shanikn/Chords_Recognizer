"""Beat This! (small0) without torch: numpy log-mel frontend + ONNX model on onnxruntime
+ numpy "minimal" postprocessing. A prototype, faithful to beat_this 1.1.0:

- frontend: beat_this.preprocessing.LogMelSpect = torchaudio MelSpectrogram(22050 Hz,
  n_fft 1024, hop 441 (50 fps), Hann window, centered with reflect padding, STFT
  normalized by 1/sqrt(n_fft), magnitude, 128 Slaney mel bands 30-11000 Hz, no filter
  normalization), then log1p(1000 * x).
- model: split_predict_aggregate (1500-frame chunks, 6-frame borders, keep_first).
- postprocessing: Postprocessor("minimal"): logits peaks within +-3 frames above 0,
  adjacent peaks merged, downbeats moved to the nearest beat. Or Postprocessor("dbn"):
  madmom's DBN on the probabilities (postprocess_dbn), which forces regular bars.
"""

from __future__ import annotations

import numpy as np
import soxr

SR, N_FFT, HOP, N_MELS, F_MIN, F_MAX, FPS = 22050, 1024, 441, 128, 30.0, 11000.0, 50
CHUNK, BORDER = 1500, 6


def _hz_to_mel(f):  # Slaney
    f = np.asarray(f, dtype=np.float64)
    mel = f / (200.0 / 3)
    logstep = np.log(6.4) / 27.0
    return np.where(f >= 1000.0, 15.0 + np.log(np.maximum(f, 1e-10) / 1000.0) / logstep, mel)


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    logstep = np.log(6.4) / 27.0
    return np.where(m >= 15.0, 1000.0 * np.exp(logstep * (m - 15.0)), m * (200.0 / 3))


def mel_filterbank() -> np.ndarray:
    """(n_freqs, n_mels), as torchaudio.functional.melscale_fbanks(norm=None, 'slaney')."""
    all_freqs = np.linspace(0, SR // 2, N_FFT // 2 + 1)
    m_pts = np.linspace(_hz_to_mel(F_MIN), _hz_to_mel(F_MAX), N_MELS + 2)
    f_pts = _mel_to_hz(m_pts)
    f_diff = np.diff(f_pts)
    slopes = f_pts[None, :] - all_freqs[:, None]
    down = -slopes[:, :-2] / f_diff[:-1]
    up = slopes[:, 2:] / f_diff[1:]
    return np.maximum(0.0, np.minimum(down, up))


_FB = mel_filterbank().astype(np.float32)
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)  # periodic


def log_mel(signal22k: np.ndarray) -> np.ndarray:
    """(frames, 128) log-mel spectrogram of a mono 22.05 kHz float signal."""
    x = np.pad(signal22k.astype(np.float32), N_FFT // 2, mode="reflect")
    frames = 1 + (len(x) - N_FFT) // HOP
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * _WINDOW, axis=1)) / np.sqrt(N_FFT)
    return np.log1p(1000.0 * (spec.astype(np.float32) @ _FB))


def frames_logits(spect: np.ndarray, session) -> tuple[np.ndarray, np.ndarray]:
    """Beat and downbeat logits per frame for the whole piece: split into chunks that
    overlap by the border, predict, drop each chunk's borders, keep the first chunk's
    frames where they overlap (beat_this.inference.split_predict_aggregate)."""
    n = len(spect)
    starts = list(range(-BORDER, n - BORDER, CHUNK - 2 * BORDER))
    if n > CHUNK - 2 * BORDER:
        starts[-1] = n - (CHUNK - BORDER)
    beat = np.full(n, -1000.0, dtype=np.float32)
    down = np.full(n, -1000.0, dtype=np.float32)
    for start in reversed(starts):  # keep_first: earlier chunks are written last
        chunk = spect[max(start, 0) : min(start + CHUNK, n)]
        pad = ((max(0, -start), max(0, min(BORDER, start + CHUNK - n))), (0, 0))
        b, d = session.run(None, {"spect": np.pad(chunk, pad)[None].astype(np.float32)})
        beat[start + BORDER : start + CHUNK - BORDER] = b[0][BORDER:-BORDER]
        down[start + BORDER : start + CHUNK - BORDER] = d[0][BORDER:-BORDER]
    return beat, down


def _peaks(logits: np.ndarray) -> np.ndarray:
    padded = np.pad(logits, 3, constant_values=-np.inf)
    window_max = np.lib.stride_tricks.sliding_window_view(padded, 7).max(axis=1)
    return np.nonzero((logits == window_max) & (logits > 0))[0]


def _dedupe(peaks: np.ndarray, width: int = 1) -> np.ndarray:
    result = []
    it = iter(int(p) for p in peaks)
    try:
        p = next(it)
    except StopIteration:
        return np.array(result)
    c = 1
    for p2 in it:
        if p2 - p <= width:
            c += 1
            p += (p2 - p) / c
        else:
            result.append(p)
            p, c = p2, 1
    result.append(p)
    return np.array(result)


def postprocess(beat_logits, down_logits) -> tuple[np.ndarray, np.ndarray]:
    beats = _dedupe(_peaks(beat_logits)) / FPS
    downs = _dedupe(_peaks(down_logits)) / FPS
    if len(beats):
        downs = np.array([beats[np.argmin(np.abs(beats - d))] for d in downs])
    return beats, np.unique(downs)


def beats_downbeats(signal44k: np.ndarray, session) -> tuple[np.ndarray, np.ndarray]:
    signal22k = soxr.resample(signal44k, in_rate=44100, out_rate=SR)
    return postprocess(*frames_logits(log_mel(signal22k), session))


# DBN postprocessing, as beat_this.model.postprocessor.Postprocessor("dbn"): madmom's
# DBNDownBeatTrackingProcessor on the model's probabilities, which forces regular bars.
_DBN = None


def _dbn():
    global _DBN
    if _DBN is None:
        from madmom.features.downbeats import DBNDownBeatTrackingProcessor

        _DBN = DBNDownBeatTrackingProcessor(
            beats_per_bar=[3, 4], min_bpm=55.0, max_bpm=215.0, fps=FPS, transition_lambda=100
        )
    return _DBN


def postprocess_dbn(beat_logits, down_logits) -> np.ndarray:
    """(beats, 2) array of [time, position in bar], position 1 = downbeat."""
    epsilon = 1e-5
    beat = 1 / (1 + np.exp(-beat_logits.astype(np.float64)))
    down = 1 / (1 + np.exp(-down_logits.astype(np.float64)))
    beat = beat * (1 - epsilon) + epsilon / 2
    down = down * (1 - epsilon) + epsilon / 2
    combined = np.vstack((np.maximum(beat - down, epsilon / 2), down)).T
    return np.asarray(_dbn()(combined)).reshape(-1, 2)


def tracked_dbn(signal44k: np.ndarray, session) -> np.ndarray:
    """Beat This! + DBN on a mono 44.1 kHz signal: (beats, 2) [time, position]."""
    signal22k = soxr.resample(signal44k, in_rate=44100, out_rate=SR)
    return postprocess_dbn(*frames_logits(log_mel(signal22k), session))


def session(path, threads: int = 4):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
