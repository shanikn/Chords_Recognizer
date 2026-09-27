"""Time offset between a recording and its annotations, from the audio and the reference
only (never from a tracker's output, which would favour that tracker).

The annotations were made on the original CDs; a YouTube upload of the same master can
start a little earlier or later. The offset (seconds to add to annotation times) is:

1. coarse, from the chords: the shift within +-3 s (0.1 s steps) at which the audio's
   chroma best matches the annotated chords (each as its pitch-class template). Chord
   changes aren't periodic, so this has one clear best shift;
2. fine, from the beats: within +-0.25 s of that, the shift (10 ms steps) at which the
   annotated beats land on the audio's onsets (spectral flux, best frame within +-10 ms).
   A beat grid alone fits almost as well shifted by whole beats, which is why it only
   refines: on its own it put Let It Be two beats off.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FPS = 100
SR = 44_100
HOP = SR // FPS
N_FFT = 2048
MAX_SHIFT = 3.0
REFINE = 0.25
CHROMA_FPS = 10


@dataclass(frozen=True)
class Alignment:
    offset: float  # seconds to add to annotation times to get audio times
    coarse: float  # the chord (chroma) estimate the beats refined
    strength: float  # best chord score above the others, in standard deviations


def onset_envelope(samples: np.ndarray) -> np.ndarray:
    """Spectral flux at 100 frames per second, normalised to mean 1."""
    padded = np.pad(samples.astype(np.float32), (N_FFT // 2, N_FFT // 2))
    frames = 1 + (len(padded) - N_FFT) // HOP
    window = np.hanning(N_FFT).astype(np.float32)
    flux = np.zeros(frames, dtype=np.float32)
    previous = None
    for start in range(0, frames, 2048):  # in blocks of frames, to keep memory small
        stop = min(start + 2048, frames)
        idx = np.arange(N_FFT)[None, :] + HOP * np.arange(start, stop)[:, None]
        spectrum = np.log1p(100 * np.abs(np.fft.rfft(padded[idx] * window, axis=1)))
        before = spectrum[:1] if previous is None else previous[None]
        rise = np.diff(np.vstack([before, spectrum]), axis=0)
        flux[start:stop] = np.maximum(rise, 0).sum(axis=1)
        previous = spectrum[-1]
    return flux / max(float(flux.mean()), 1e-9)


def chroma(samples: np.ndarray) -> np.ndarray:
    """(frames, 12) unit-length pitch-class profiles at CHROMA_FPS, from 60-2100 Hz."""
    hop = SR // CHROMA_FPS
    n_fft = 8192
    padded = np.pad(samples.astype(np.float32), (n_fft // 2, n_fft // 2))
    frames = 1 + (len(padded) - n_fft) // hop
    freqs = np.fft.rfftfreq(n_fft, 1 / SR)
    band = (freqs >= 60) & (freqs <= 2100)
    classes = np.round(12 * np.log2(freqs[band] / 440.0) + 69).astype(int) % 12
    onehot = np.zeros((band.sum(), 12), dtype=np.float32)
    onehot[np.arange(band.sum()), classes] = 1
    window = np.hanning(n_fft).astype(np.float32)
    out = np.zeros((frames, 12), dtype=np.float32)
    for start in range(0, frames, 512):
        stop = min(start + 512, frames)
        idx = np.arange(n_fft)[None, :] + hop * np.arange(start, stop)[:, None]
        magnitude = np.abs(np.fft.rfft(padded[idx] * window, axis=1))[:, band]
        out[start:stop] = np.log1p(100 * magnitude) @ onehot
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


def chord_templates(intervals: np.ndarray, labels: list[str], frames: int) -> np.ndarray:
    """(frames, 12) unit pitch-class template per annotation frame; zeros for N/X."""
    import mir_eval

    out = np.zeros((frames, 12), dtype=np.float32)
    for (start, end), label in zip(intervals, labels, strict=True):
        root, bitmap, _ = mir_eval.chord.encode(label)
        if root < 0 or not bitmap.any():
            continue
        template = np.roll(bitmap.astype(np.float32), root)
        a, b = int(round(start * CHROMA_FPS)), int(round(end * CHROMA_FPS))
        out[max(a, 0) : min(b, frames)] = template / np.linalg.norm(template)
    return out


def chord_shift_scores(audio_chroma, templates, shifts: np.ndarray) -> np.ndarray:
    """Mean cosine similarity of annotation frames k and audio frames k + shift."""
    annotated = np.nonzero(templates.any(axis=1))[0]
    scores = []
    for shift in shifts:
        k = annotated[(annotated + shift >= 0) & (annotated + shift < len(audio_chroma))]
        scores.append(
            float((audio_chroma[k + shift] * templates[k]).sum(axis=1).mean()) if len(k) else 0.0
        )
    return np.array(scores)


def beat_score(envelope: np.ndarray, beats: np.ndarray, shift: float) -> float:
    """Mean onset strength at the beats shifted by `shift` (best frame within +-1)."""
    frames = np.round((beats + shift) * FPS).astype(int)
    frames = frames[(frames >= 1) & (frames < len(envelope) - 1)]
    if not len(frames):
        return 0.0
    near = np.stack([envelope[frames - 1], envelope[frames], envelope[frames + 1]])
    return float(near.max(axis=0).mean())


def estimate_offset(
    samples: np.ndarray, beats: np.ndarray, intervals: np.ndarray, labels: list[str]
) -> Alignment:
    """Offset of the annotations in `samples` (mono, 44.1 kHz): see the module docstring."""
    audio_chroma = chroma(samples)
    frames = int(np.ceil(float(np.max(intervals)) * CHROMA_FPS)) + 1
    templates = chord_templates(np.asarray(intervals), list(labels), frames)
    steps = np.arange(-int(MAX_SHIFT * CHROMA_FPS), int(MAX_SHIFT * CHROMA_FPS) + 1)
    chord_scores = chord_shift_scores(audio_chroma, templates, steps)
    coarse = float(steps[int(np.argmax(chord_scores))]) / CHROMA_FPS
    # How far the best shift stands out from the others, in standard deviations.
    strength = float((chord_scores.max() - chord_scores.mean()) / max(chord_scores.std(), 1e-9))

    envelope = onset_envelope(samples)
    fine = np.round(np.arange(coarse - REFINE, coarse + REFINE + 1e-9, 1 / FPS), 2)
    beat_scores = [beat_score(envelope, beats, s) for s in fine]
    return Alignment(float(fine[int(np.argmax(beat_scores))]), round(coarse, 2), strength)
