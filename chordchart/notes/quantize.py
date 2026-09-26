"""Put transcribed notes on the chord chart's grid, and drop the noise.

The grid is built from the chart's own bars (Song.bars), so the piano roll lines up
with the chord sheet exactly: every beat is split into 4 sixteenths. basic-pitch's
notes start and end anywhere; each end is moved to the nearest grid point.

Filtering happens before snapping, on what basic-pitch heard:
- notes shorter than half a 16th are blips (a pick noise, a bleed from another stem);
- notes quieter than MIN_VELOCITY are mostly bleed and harmonics.
After snapping, two notes of the same pitch that overlap are one note.
"""

from __future__ import annotations

import bisect
import statistics
from collections.abc import Sequence
from dataclasses import replace

from chordchart.model import Bar
from chordchart.notes.model import Note

STEPS_PER_BEAT = 4
# basic-pitch's velocity is 127 x its amplitude estimate. Notes need amplitude above
# its frame threshold (0.3) to exist at all, so 40 only removes the faintest.
MIN_VELOCITY = 40
# A bar whose length is within this fraction of a regular bar is a full bar.
FULL_BAR_TOLERANCE = 0.25


def beat_times(bars: Sequence[Bar], meter: int) -> list[float]:
    """Every beat of the chart, then the end of the last bar.

    Regular bars are split evenly into `meter` beats. A pickup bar (index 0) counts
    back from its end, and a short last bar (the song stops mid-bar) counts forward,
    both at the typical beat length, since their number of beats isn't known.
    """
    if not bars:
        return []
    # Only the pickup and the last bar can be partial, so the others set the length.
    full = [b for b in bars[:-1] if b.index > 0] or [bars[-1]]
    typical = statistics.median(b.end - b.start for b in full)
    beat = typical / meter
    times: list[float] = []
    for bar in bars:
        length = bar.end - bar.start
        if abs(length - typical) <= FULL_BAR_TOLERANCE * typical:
            step = length / meter
            times += [bar.start + k * step for k in range(meter)]
        else:
            count = max(1, round(length / beat))
            if bar.index == 0:
                times += [bar.end - (count - k) * beat for k in range(count)]
            else:
                times += [t for k in range(count) if (t := bar.start + k * beat) < bar.end - 1e-6]
    times.append(bars[-1].end)
    return times


def grid_from_bars(bars: Sequence[Bar], meter: int, steps_per_beat: int = STEPS_PER_BEAT):
    """The chart's 16th-note grid: sorted times, the last one being the chart's end."""
    beats = beat_times(bars, meter)
    grid = [
        a + k * (b - a) / steps_per_beat
        for a, b in zip(beats, beats[1:], strict=False)
        for k in range(steps_per_beat)
    ]
    return [*grid, beats[-1]] if beats else []


def quantize(notes: Sequence[Note], grid: Sequence[float]) -> list[Note]:
    """Filter `notes`, snap them onto `grid`, merge same-pitch overlaps; sort by time."""
    if len(grid) < 2:
        return []
    snapped: list[Note] = []
    for note in notes:
        if note.velocity < MIN_VELOCITY or note.start >= grid[-1] or note.end <= grid[0]:
            continue
        i = min(max(bisect.bisect_right(grid, note.start) - 1, 0), len(grid) - 2)
        if note.end - note.start < (grid[i + 1] - grid[i]) / 2:
            continue
        first = _nearest(grid, note.start)
        last = max(_nearest(grid, note.end), first + 1)
        if first >= len(grid) - 1:  # starts on the chart's very end
            continue
        snapped.append(
            replace(note, start=grid[first], end=grid[last], step=first, steps=last - first)
        )
    return _merge(snapped)


def _nearest(grid: Sequence[float], t: float) -> int:
    i = bisect.bisect_left(grid, t)
    if i == 0:
        return 0
    if i == len(grid):
        return len(grid) - 1
    return i if grid[i] - t < t - grid[i - 1] else i - 1


def _merge(notes: list[Note]) -> list[Note]:
    merged: list[Note] = []
    open_by_pitch: dict[int, int] = {}  # pitch -> index in merged of its latest note
    for note in sorted(notes, key=lambda n: (n.step, n.pitch)):
        j = open_by_pitch.get(note.pitch)
        if j is not None and note.step < merged[j].step + merged[j].steps:
            prev = merged[j]
            end_step = max(prev.step + prev.steps, note.step + note.steps)
            merged[j] = replace(
                prev,
                end=max(prev.end, note.end),
                steps=end_step - prev.step,
                velocity=max(prev.velocity, note.velocity),
            )
            continue
        open_by_pitch[note.pitch] = len(merged)
        merged.append(note)
    return merged
