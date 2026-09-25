"""Turn the recognizer's timed segments into bars of a readable chart.

**Why this step exists.** The model thinks in seconds, but musicians think in bars and
beats. The model puts a chord boundary wherever the audio evidence changes, which is
often a little before or after the beat (a strum that anticipates the bar, a sustained
note that rings over). Occasionally it also puts a short spurious chord mid-bar. Printed
as-is, that gives a chart nobody can play from.

We fix it in two snapping steps:

1. **beat_sync:** give each beat the one chord that covers most of it. Chord changes
   can now only happen on beats.
2. **group_bars:** start a new bar at every downbeat, and inside a bar allow changes
   only at the *split points* the meter suggests (beats 1 and 3 in 4/4, beats 1 and 3
   in 3/4, beats 1 and 4 in 6/8 counted in eighths). Each region between split points
   gets its majority chord.

Snapping costs some accuracy: a real change on beat 2 of a 4/4 bar gets moved. The
eval harness (milestone 4) scores every stage separately to measure that cost.
Smoothing of short chords, merging and vocabulary simplification come in milestone 5.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise

from chordchart.beats import Beats
from chordchart.model import Bar, ChordEvent, Segment
from chordchart.symbols import harte_to_symbol


def split_points(beats_per_bar: int) -> tuple[int, ...]:
    """0-based beats within a bar where a chord change is allowed (spec §4).

    Compound meters (6, 9, 12 beats) group beats in threes. Simple meters group them
    in twos. 3/4 is the special case (0, 2): the "C . G" waltz split.
    """
    if beats_per_bar <= 2:
        return tuple(range(beats_per_bar))
    if beats_per_bar == 3:
        return (0, 2)
    if beats_per_bar % 3 == 0:
        return tuple(range(0, beats_per_bar, 3))
    if beats_per_bar % 2 == 0:
        return tuple(range(0, beats_per_bar, 2))
    return (0,)


@dataclass(frozen=True)
class PostprocessOptions:
    # beats-per-bar -> split points, replacing split_points() for that meter
    split_overrides: dict[int, tuple[int, ...]] = field(default_factory=dict)

    def split_points_for(self, beats_per_bar: int) -> tuple[int, ...]:
        return self.split_overrides.get(beats_per_bar, split_points(beats_per_bar))


def beat_sync(segments: list[Segment], beat_times: Sequence[float], end_time: float) -> list[str]:
    """Label each beat [t_i, t_i+1) with the segment label that overlaps it most.

    The last beat runs to `end_time`. A beat no segment touches gets "N".
    """
    labels = []
    for start, end in pairwise([*beat_times, end_time]):
        overlap: dict[str, float] = defaultdict(float)
        for seg in segments:
            amount = min(end, seg.end) - max(start, seg.start)
            if amount > 0:
                overlap[seg.label] += amount
        labels.append(max(overlap, key=overlap.__getitem__) if overlap else "N")
    return labels


def group_bars(
    beats: Beats,
    beat_labels: list[str],
    duration: float,
    options: PostprocessOptions | None = None,
) -> list[Bar]:
    """Group beats into bars (one per downbeat) and choose each bar's chords.

    Beats before the first downbeat form a pickup bar with index 0. Full bars are
    numbered from 1. Returns [] if there is no downbeat at all.
    """
    options = options or PostprocessOptions()
    points = options.split_points_for(beats.meter)
    starts = [i for i, p in enumerate(beats.positions) if p == 1]
    if not starts:
        return []
    first_index = 1
    if starts[0] > 0:
        starts.insert(0, 0)
        first_index = 0

    bars = []
    for n, (a, b) in enumerate(pairwise([*starts, len(beats.times)])):
        end = beats.times[b] if b < len(beats.times) else duration
        chords = _quantize_bar(beats.times[a:b], beats.positions[a:b], beat_labels[a:b], points)
        bars.append(Bar(index=first_index + n, start=beats.times[a], end=end, chords=chords))
    return bars


def _quantize_bar(
    times: Sequence[float],
    positions: Sequence[int],
    labels: Sequence[str],
    points: tuple[int, ...],
) -> list[ChordEvent]:
    # Assign each beat to the last split point at or before it. Positions (not list
    # indices) are used, so a pickup beat "4" lands in the region of beat 3 (0-based).
    regions: dict[int, list[int]] = defaultdict(list)
    for i, pos in enumerate(positions):
        regions[max(p for p in points if p <= pos - 1)].append(i)

    events: list[ChordEvent] = []
    for point in sorted(regions):
        idx = regions[point]
        # most_common is stable, so on a tie the label of the earliest beat wins.
        label = Counter(labels[i] for i in idx).most_common(1)[0][0]
        if events and events[-1].harte == label:
            continue
        first = idx[0]
        events.append(
            ChordEvent(
                beat=positions[first] - 1,
                time=times[first],
                symbol=harte_to_symbol(label),
                harte=label,
            )
        )
    return events


def labels_to_segments(
    times: Sequence[float], labels: Sequence[str], end_time: float
) -> list[Segment]:
    """Per-beat labels -> timed segments, merging repeats (used for debug/eval output)."""
    segments: list[Segment] = []
    for (start, stop), label in zip(pairwise([*times, end_time]), labels, strict=True):
        if segments and segments[-1].label == label:
            segments[-1] = Segment(segments[-1].start, stop, label)
        else:
            segments.append(Segment(start, stop, label))
    return segments
