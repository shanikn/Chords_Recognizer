"""The beat trackers the evaluation compares. Each is a factory returning a beats_fn for
chordchart.pipeline.analyze(beats_fn=...): audio (the loaded 44.1 kHz mono Signal) ->
Beats. madmom is what chordchart uses; the others are candidates, not defaults.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import numpy as np

from chordchart.beats import Beats, bpm_from_times, meter_from_positions, track_beats

ROOT = Path(__file__).resolve().parents[1]
BEAT_THIS_ONNX = ROOT / "experiments" / "out" / "beat_this" / "beat_this_small0.onnx"
DOWNLOAD_STEP = (
    "uv run --no-project --python 3.12 --with beat-this --with onnx --with onnxruntime "
    '--with "torch==2.14.0" python experiments/beat_this/export.py'
)


def madmom(processors) -> Callable:
    """chordchart's own beat tracking (RNNDownBeatProcessor + DBN, 3/4 and 4/4)."""

    def beats_fn(audio) -> Beats:
        return track_beats(audio, (3, 4), processors)

    return beats_fn


def beat_this_dbn(processors, threads: int = 4) -> Callable:
    """Beat This! small0 on onnxruntime (experiments/beat_this, verified against the
    original), with madmom's DBN as its postprocessing (3/4 and 4/4, 55-215 BPM)."""
    if not BEAT_THIS_ONNX.exists():
        raise SystemExit(
            f"Beat This! model missing: {BEAT_THIS_ONNX}\nrun the download step:\n  {DOWNLOAD_STEP}"
        )
    sys.path.insert(0, str(ROOT / "experiments" / "beat_this"))
    import beat_this_onnx as bt

    session = bt.session(BEAT_THIS_ONNX, threads)

    def beats_fn(audio) -> Beats:
        samples = np.asarray(audio, dtype=np.float32) / 32768.0  # the Signal is int16
        tracked = bt.tracked_dbn(samples, session)
        times = [float(t) for t in tracked[:, 0]]
        positions = [int(p) for p in tracked[:, 1]]
        return Beats(times, positions, bpm_from_times(times), meter_from_positions(positions))

    return beats_fn


TRACKERS = {"madmom": madmom, "beat_this_dbn": beat_this_dbn}
