"""Beat and downbeat tracking: where the beats are, and which of them start a bar.

A chart is organised in bars, so before placing any chord we need the song's
"skeleton": the time of every beat, and each beat's position in its bar (1 = the
downbeat).

The default tracker (track_beats) is Beat This! + DBN: a transformer (beat_this.py)
says how likely a beat and a downbeat are in every frame, and madmom's DBN (step 2
below) turns that into steady beats and whole bars. It replaced madmom's own RNN on
2026-09-28 after the evaluation (evaluate/): better beats, downbeats and meter, and
about 3x faster. madmom's tracker is still available as track_beats_madmom, e.g.
analyze(beats_fn=partial(track_beats_madmom, processors=...)).

madmom's own tracker works in two stages:

1. **RNNDownBeatProcessor**, a recurrent neural network. It reads the audio as a
   spectrogram (100 frames per second) and outputs two numbers per frame: the
   probability that a beat falls here, and the probability that a downbeat falls here.
   The RNN hears onsets, accents and harmonic changes, but its output is noisy: a
   peak here, a missing beat there.

2. **DBNDownBeatTrackingProcessor**, a dynamic Bayesian network. It models a bar as a
   cycle of N beats (N from `beats_per_bar`) moving at a tempo that can only drift
   slowly. Viterbi decoding picks the single most probable path through
   (tempo, position-in-bar) given the RNN's activations. Because the path must be
   musically plausible, weak or missing beats are filled in at the right spacing, and
   the bar count stays consistent. That's why we get evenly spaced beats and a stable
   meter from noisy evidence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from chordchart.fetch import model_input

if TYPE_CHECKING:
    from chordchart.processors import Processors

FPS = 100  # frames per second of madmom's RNN activations


@dataclass(frozen=True)
class Beats:
    times: list[float]  # seconds
    positions: list[int]  # 1-based position of each beat in its bar; 1 = downbeat
    bpm: float
    meter: int  # beats per bar


def track_beats(
    audio,
    beats_per_bar: Sequence[int] = (3, 4),
    processors: Processors | None = None,
) -> Beats:
    """Beats and downbeats with Beat This! + DBN (the default). `audio` is the loaded
    Signal (44.1 kHz mono int16) or a path to such a WAV. Reuses `processors` if given
    (and built for the same `beats_per_bar`); otherwise loads the model for this call."""
    from chordchart import beat_this

    if processors is not None and processors.beats_per_bar == tuple(beats_per_bar):
        model, dbn = processors.beat_this, processors.beat_this_dbn
    else:
        model, dbn = beat_this.BeatThisModel(), beat_this.make_dbn(beats_per_bar)
    logits = model.logits(_samples(audio))
    tracked = np.asarray(dbn(beat_this.dbn_activations(*logits))).reshape(-1, 2)
    return _beats(tracked)


def track_beats_madmom(
    audio,
    beats_per_bar: Sequence[int] = (3, 4),
    processors: Processors | None = None,
) -> Beats:
    """Beats and downbeats with madmom's RNN + DBN (the default until 2026-09-28)."""
    if processors is not None and processors.beats_per_bar == tuple(beats_per_bar):
        rnn, tracker = processors.downbeat_rnn, processors.downbeat_dbn
    else:
        from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor

        rnn = RNNDownBeatProcessor()
        tracker = DBNDownBeatTrackingProcessor(beats_per_bar=list(beats_per_bar), fps=FPS)
    activations = rnn(model_input(audio))
    return _beats(np.asarray(tracker(activations)).reshape(-1, 2))


def _samples(audio) -> np.ndarray:
    """The audio as float32 in [-1, 1]: a loaded Signal (int16) or a WAV path."""
    if isinstance(audio, str | Path):
        from chordchart.fetch import read_wav

        return read_wav(Path(audio))
    return np.asarray(audio, dtype=np.float32) / 32768.0


def _beats(tracked: np.ndarray) -> Beats:
    times = [float(t) for t in tracked[:, 0]]
    positions = [int(p) for p in tracked[:, 1]]
    return Beats(times, positions, bpm_from_times(times), meter_from_positions(positions))


def bpm_from_times(times: Sequence[float]) -> float:
    """Tempo from the median beat interval. The median ignores the odd glitch."""
    if len(times) < 2:
        return 0.0
    return 60.0 / float(np.median(np.diff(times)))


def meter_from_positions(positions: Sequence[int]) -> int:
    """Most common number of beats between consecutive downbeats."""
    downbeats = [i for i, p in enumerate(positions) if p == 1]
    lengths = [b - a for a, b in pairwise(downbeats)]
    if not lengths:
        return max(positions, default=4)
    return Counter(lengths).most_common(1)[0][0]
