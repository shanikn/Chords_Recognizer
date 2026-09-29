"""Beat This! (CPJKU, small0), the default beat and downbeat tracker, without torch.

Beat This! is a transformer that reads a log-mel spectrogram (50 frames per second) and
outputs, per frame, how likely a beat and a downbeat are. It runs here as an ONNX model
(models/beat_this_small0.onnx, exported from the published checkpoint by
experiments/beat_this/export.py) on onnxruntime, with the frontend in numpy. Every step
reproduces beat_this 1.1.0 exactly; experiments/beat_this/verify.py checks it.

- frontend: torchaudio's MelSpectrogram as Beat This! configures it (22050 Hz, n_fft
  1024, hop 441, Hann window, centered with reflect padding, STFT scaled by
  1/sqrt(n_fft), magnitude, 128 Slaney mel bands 30-11000 Hz), then log1p(1000 * x);
- the model sees 1500-frame (30 s) chunks overlapping by 6 frames at each side; each
  chunk's border frames are dropped and the first chunk wins where they overlap;
- the logits become probabilities for madmom's DBNDownBeatTrackingProcessor (as Beat
  This!'s "dbn" postprocessing, with a stiffer tempo: see DBN_TRANSITION_LAMBDA), which
  picks steady beats and whole bars.

Evaluated against madmom's own tracker on 10 annotated Beatles songs (evaluate/,
2026-09-27): beat F 0.822 vs 0.800, CMLt 0.895 vs 0.766, downbeat F 0.816 vs 0.800,
meter right 10/10 vs 9/10, about 2.9x faster. Code and weights: MIT (models/).
"""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

MODEL = Path(__file__).with_name("models") / "beat_this_small0.onnx"
MODEL_SHA256 = "8388f6ec6b071fe40e5a901162066c7c97138214a0147ec2fa492c23a26be2d6"
SR, N_FFT, HOP, N_MELS, F_MIN, F_MAX = 22050, 1024, 441, 128, 30.0, 11000.0
FPS = 50  # frames per second of the model's output
CHUNK, BORDER = 1500, 6
# The DBN postprocessing's settings, as beat_this.model.postprocessor.Postprocessor,
# except the tempo stiffness: Beat This! uses transition_lambda 100, which let it switch
# metrical level mid-song on 3 of 11 real songs (bars doubling or halving for a
# section). 300 removed that on two of them and matched madmom on others, with the
# annotated Beatles scores unchanged; 1000 broke those (tempo drift). See
# experiments/beat_this/dbn_lambda.py (2026-09-28).
DBN_MIN_BPM, DBN_MAX_BPM, DBN_TRANSITION_LAMBDA = 55.0, 215.0, 300


def _hz_to_mel(f):  # Slaney
    f = np.asarray(f, dtype=np.float64)
    logstep = np.log(6.4) / 27.0
    return np.where(
        f >= 1000.0, 15.0 + np.log(np.maximum(f, 1e-10) / 1000.0) / logstep, f / (200.0 / 3)
    )


def _mel_to_hz(m):
    m = np.asarray(m, dtype=np.float64)
    logstep = np.log(6.4) / 27.0
    return np.where(m >= 15.0, 1000.0 * np.exp(logstep * (m - 15.0)), m * (200.0 / 3))


def _mel_filterbank() -> np.ndarray:
    """(n_freqs, n_mels) as torchaudio.functional.melscale_fbanks(norm=None, 'slaney')."""
    all_freqs = np.linspace(0, SR // 2, N_FFT // 2 + 1)
    f_pts = _mel_to_hz(np.linspace(_hz_to_mel(F_MIN), _hz_to_mel(F_MAX), N_MELS + 2))
    f_diff = np.diff(f_pts)
    slopes = f_pts[None, :] - all_freqs[:, None]
    down = -slopes[:, :-2] / f_diff[:-1]
    up = slopes[:, 2:] / f_diff[1:]
    return np.maximum(0.0, np.minimum(down, up))


_FILTERBANK = _mel_filterbank().astype(np.float32)
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)


def log_mel(signal22k: np.ndarray) -> np.ndarray:
    """(frames, 128) log-mel spectrogram of a mono 22.05 kHz float signal."""
    x = np.pad(signal22k.astype(np.float32), N_FFT // 2, mode="reflect")
    frames = 1 + (len(x) - N_FFT) // HOP
    out = np.empty((frames, N_MELS), dtype=np.float32)
    for start in range(0, frames, 4096):  # in blocks, to keep memory small
        stop = min(start + 4096, frames)
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(start, stop)[:, None]
        spec = np.abs(np.fft.rfft(x[idx] * _WINDOW, axis=1)) / np.sqrt(N_FFT)
        out[start:stop] = spec.astype(np.float32) @ _FILTERBANK
    return np.log1p(1000.0 * out)


class BeatThisModel:
    """The ONNX model on onnxruntime. One per process is enough; calls are thread-safe."""

    def __init__(self, threads: int = 1, path: Path = MODEL) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, threads)
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self._lock = threading.Lock()
        # onnxruntime keeps the memory of its largest run in an arena for reuse: ~1.3 GB
        # for a 30 s chunk, held for as long as the app runs. The chunks of one song reuse
        # it; the last chunk's run then hands it back (the arena shrinks after that run).
        # Same results; no slower than keeping it (experiments/profile/ort_arena.py).
        self._release = ort.RunOptions()
        self._release.add_run_config_entry("memory.enable_memory_arena_shrinkage", "cpu:0")

    def logits(self, samples44k: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Beat and downbeat logits per frame (FPS) for a mono 44.1 kHz float signal."""
        import soxr

        spect = log_mel(soxr.resample(samples44k, in_rate=44100, out_rate=SR))
        n = len(spect)
        starts = list(range(-BORDER, n - BORDER, CHUNK - 2 * BORDER))
        if n > CHUNK - 2 * BORDER:
            starts[-1] = n - (CHUNK - BORDER)
        beat = np.full(n, -1000.0, dtype=np.float32)
        down = np.full(n, -1000.0, dtype=np.float32)
        with self._lock:
            for i, start in enumerate(reversed(starts)):  # the first chunk wins where they overlap
                chunk = spect[max(start, 0) : min(start + CHUNK, n)]
                pad = ((max(0, -start), max(0, min(BORDER, start + CHUNK - n))), (0, 0))
                chunk = np.pad(chunk, pad)[None].astype(np.float32)
                last = i == len(starts) - 1
                b, d = self.session.run(None, {"spect": chunk}, self._release if last else None)
                beat[start + BORDER : start + CHUNK - BORDER] = b[0][BORDER:-BORDER]
                down[start + BORDER : start + CHUNK - BORDER] = d[0][BORDER:-BORDER]
        return beat, down


def make_dbn(beats_per_bar, threads: int = 1):
    """madmom's DBN with Beat This!'s settings. With threads > 1 it decodes the meters
    (3/4 and 4/4) in parallel worker processes."""
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor

    return DBNDownBeatTrackingProcessor(
        beats_per_bar=list(beats_per_bar),
        min_bpm=DBN_MIN_BPM,
        max_bpm=DBN_MAX_BPM,
        fps=FPS,
        transition_lambda=DBN_TRANSITION_LAMBDA,
        num_threads=max(1, min(threads, len(beats_per_bar))),
    )


def dbn_activations(beat_logits: np.ndarray, down_logits: np.ndarray) -> np.ndarray:
    """(frames, 2) [beat-but-not-downbeat, downbeat] probabilities, as Beat This! feeds
    madmom's DBN (kept off 0 and 1, which the DBN can't take)."""
    epsilon = 1e-5
    beat = 1 / (1 + np.exp(-beat_logits.astype(np.float64)))
    down = 1 / (1 + np.exp(-down_logits.astype(np.float64)))
    beat = beat * (1 - epsilon) + epsilon / 2
    down = down * (1 - epsilon) + epsilon / 2
    return np.vstack((np.maximum(beat - down, epsilon / 2), down)).T
