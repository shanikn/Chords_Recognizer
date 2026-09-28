"""The beat trackers the evaluation compares. Each is a factory returning a beats_fn for
chordchart.pipeline.analyze(beats_fn=...): audio (the loaded 44.1 kHz mono Signal) ->
Beats. Both are chordchart's own code: Beat This! + DBN is the default since
2026-09-28, madmom's RNN + DBN the previous one.
"""

from __future__ import annotations

from collections.abc import Callable

from chordchart.beats import Beats, track_beats, track_beats_madmom


def madmom(processors) -> Callable:
    """madmom's RNNDownBeatProcessor + DBN (3/4 and 4/4): the default until 2026-09-28."""

    def beats_fn(audio) -> Beats:
        return track_beats_madmom(audio, (3, 4), processors)

    return beats_fn


def beat_this_dbn(processors) -> Callable:
    """Beat This! small0 on onnxruntime + madmom's DBN (chordchart.beat_this): the default."""

    def beats_fn(audio) -> Beats:
        return track_beats(audio, (3, 4), processors)

    return beats_fn


TRACKERS = {"madmom": madmom, "beat_this_dbn": beat_this_dbn}
