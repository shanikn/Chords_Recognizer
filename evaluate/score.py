"""Scores with mir_eval: chords per pipeline stage, beats, downbeats and meter."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from itertools import pairwise

import numpy as np

from chordchart.beats import Beats
from chordchart.model import Segment, Song

CHORD_METRICS = ("majmin", "root", "mirex", "seg")


def segments_to_arrays(segments: Sequence[Segment]) -> tuple[np.ndarray, list[str]]:
    kept = [s for s in segments if s.end > s.start]
    return np.array([[s.start, s.end] for s in kept]).reshape(-1, 2), [s.label for s in kept]


def chart_segments(song: Song) -> list[Segment]:
    """The final chart as timed chords: each chord event lasts until the next one, and the
    last until the end of the chart (spec §7, `chart` stage). A chord repeated at the
    start of a bar isn't a change, so it continues the previous segment."""
    events = []
    for bar in song.bars:
        for e in bar.chords:
            if not events or events[-1][1] != e.harte:
                events.append((e.time, e.harte))
    end = song.bars[-1].end if song.bars else 0.0
    return [
        Segment(t, t_next, label)
        for (t, label), (t_next, _) in zip(events, [*events[1:], (end, None)], strict=True)
        if t_next > t
    ]


def chord_scores(ref_intervals, ref_labels, segments: Sequence[Segment]) -> dict[str, float]:
    """mir_eval.chord metrics: majmin (the main one), root, mirex, and segmentation."""
    import mir_eval

    est_intervals, est_labels = segments_to_arrays(segments)
    scores = mir_eval.chord.evaluate(ref_intervals, ref_labels, est_intervals, est_labels)
    return {name: float(scores[name]) for name in CHORD_METRICS}


def meter_of(positions: Sequence[int]) -> int:
    """Most common number of beats from one downbeat to the next."""
    downbeats = [i for i, p in enumerate(positions) if p == 1]
    lengths = [b - a for a, b in pairwise(downbeats)]
    return Counter(lengths).most_common(1)[0][0] if lengths else 0


def beat_scores(ref_times, ref_positions, est: Beats) -> dict[str, float | int | bool]:
    """mir_eval.beat (first 5 s trimmed, as standard): F-measure (+-70 ms), CMLt (only the
    annotated metrical level) and AMLt (double/half tempo and off-beat also accepted);
    downbeat F-measure; meter. `octave` flags "right beats at the wrong level"."""
    import mir_eval

    est_times = np.asarray(est.times, dtype=float)
    beats = mir_eval.beat.evaluate(np.asarray(ref_times, dtype=float), est_times)
    ref_down = np.asarray([t for t, p in zip(ref_times, ref_positions, strict=True) if p == 1])
    est_down = np.asarray([t for t, p in zip(est.times, est.positions, strict=True) if p == 1])
    trim = mir_eval.beat.trim_beats
    down_f = float(mir_eval.beat.f_measure(trim(ref_down), trim(est_down)))
    cmlt = float(beats["Correct Metric Level Total"])
    amlt = float(beats["Any Metric Level Total"])
    return {
        "beat_f": float(beats["F-measure"]),
        "cmlt": cmlt,
        "amlt": amlt,
        "downbeat_f": down_f,
        "meter_ref": meter_of(list(ref_positions)),
        "meter_est": int(est.meter),
        "octave": amlt >= 0.8 and cmlt < 0.5,
    }


def weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = float(sum(weights))
    return float(sum(v * w for v, w in zip(values, weights, strict=True)) / total) if total else 0.0


def shift_reference(intervals, labels, beat_times, beat_positions, offset: float, duration: float):
    """The annotations moved onto the recording's timeline (add `offset`) and clipped to
    it, [0, duration]: segments and beats outside it are dropped, the rest trimmed."""
    shifted = np.asarray(intervals, dtype=float) + offset
    keep = (shifted[:, 1] > 0) & (shifted[:, 0] < duration)
    clipped = np.clip(shifted[keep], 0.0, duration)
    kept_labels = [lab for lab, k in zip(labels, keep, strict=True) if k]
    nonempty = clipped[:, 1] > clipped[:, 0]
    times = np.asarray(beat_times, dtype=float) + offset
    inside = (times >= 0) & (times <= duration)
    return (
        clipped[nonempty],
        [lab for lab, k in zip(kept_labels, nonempty, strict=True) if k],
        times[inside],
        np.asarray(beat_positions)[inside],
    )
