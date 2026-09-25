"""Beat and downbeat tracking: where the beats are, and which of them start a bar.

A chart is organised in bars, so before placing any chord we need the song's
"skeleton": the time of every beat, and each beat's position in its bar (1 = the
downbeat).

madmom does this in two stages:

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

FPS = 100  # frames per second of the RNN activations


@dataclass(frozen=True)
class Beats:
    times: list[float]  # seconds
    positions: list[int]  # 1-based position of each beat in its bar; 1 = downbeat
    bpm: float
    meter: int  # beats per bar


def track_beats(
    wav_path: Path,
    beats_per_bar: Sequence[int] = (3, 4),
    processors: Processors | None = None,
) -> Beats:
    """Beats and downbeats. Reuses `processors` if given (and built for the same
    `beats_per_bar`); otherwise builds single-threaded processors for this call."""
    if processors is not None and processors.beats_per_bar == tuple(beats_per_bar):
        rnn, tracker = processors.downbeat_rnn, processors.downbeat_dbn
    else:
        from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor

        rnn = RNNDownBeatProcessor()
        tracker = DBNDownBeatTrackingProcessor(beats_per_bar=list(beats_per_bar), fps=FPS)
    activations = rnn(model_input(wav_path))
    tracked = np.asarray(tracker(activations)).reshape(-1, 2)
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
