"""Notes: the 16th-note grid from the chart's bars, and snapping notes onto it."""

import pytest

from chordchart.model import Bar
from chordchart.notes.model import Note
from chordchart.notes.quantize import MIN_VELOCITY, beat_times, grid_from_bars, quantize


def _bars(*spans, first_index=1):
    return [Bar(index=first_index + i, start=a, end=b, chords=[]) for i, (a, b) in enumerate(spans)]


def test_full_bars_are_split_into_meter_beats():
    bars = _bars((0.0, 2.0), (2.0, 4.4))
    assert beat_times(bars, meter=4) == pytest.approx([0, 0.5, 1, 1.5, 2, 2.6, 3.2, 3.8, 4.4])


def test_pickup_beats_count_back_from_the_first_downbeat():
    # A 2-beat pickup before a 4/4 bar of 0.5 s beats.
    bars = [Bar(0, 1.0, 2.0, []), *_bars((2.0, 4.0))]
    assert beat_times(bars, meter=4) == pytest.approx([1, 1.5, 2, 2.5, 3, 3.5, 4])


def test_partial_last_bar_keeps_the_beat_length():
    # The song ends 1.5 beats into the last bar: beats continue at 0.5 s.
    bars = _bars((0.0, 2.0), (2.0, 2.75))
    assert beat_times(bars, meter=4) == pytest.approx([0, 0.5, 1, 1.5, 2, 2.5, 2.75])


def test_grid_has_four_steps_per_beat():
    grid = grid_from_bars(_bars((0.0, 2.0)), meter=4)
    assert len(grid) == 17  # 16 sixteenths and the end of the bar
    assert grid[:3] == pytest.approx([0, 0.125, 0.25])
    assert grid[-1] == pytest.approx(2.0)


GRID = [i * 0.125 for i in range(33)]  # two 4/4 bars at 120 BPM, 0.125 s per 16th


def _note(start, end, pitch=60, velocity=90):
    return Note(start=start, end=end, pitch=pitch, velocity=velocity)


def test_notes_snap_to_the_nearest_sixteenths():
    [note] = quantize([_note(0.26, 0.74)], GRID)
    assert (note.step, note.steps) == (2, 4)
    assert (note.start, note.end) == pytest.approx((0.25, 0.75))
    assert (note.pitch, note.velocity) == (60, 90)


def test_a_note_that_snaps_to_nothing_keeps_one_step():
    [note] = quantize([_note(0.30, 0.37)], GRID)  # 70 ms: both ends snap to step 2
    assert (note.step, note.steps) == (2, 1)


def test_very_short_notes_are_dropped():
    assert quantize([_note(0.30, 0.35)], GRID) == []  # 50 ms < half a 16th (62.5 ms)


def test_quiet_notes_are_dropped():
    assert quantize([_note(0.25, 0.75, velocity=MIN_VELOCITY - 1)], GRID) == []
    assert len(quantize([_note(0.25, 0.75, velocity=MIN_VELOCITY)], GRID)) == 1


def test_overlapping_notes_of_one_pitch_merge():
    [note] = quantize([_note(0.0, 0.5, velocity=70), _note(0.4, 1.0, velocity=100)], GRID)
    assert (note.step, note.steps, note.velocity) == (0, 8, 100)


def test_repeated_notes_that_touch_stay_separate():
    notes = quantize([_note(0.0, 0.5), _note(0.5, 1.0)], GRID)
    assert [(n.step, n.steps) for n in notes] == [(0, 4), (4, 4)]


def test_notes_outside_the_grid_are_clipped_or_dropped():
    notes = quantize([_note(-1.0, 0.5), _note(3.9, 5.0), _note(4.5, 5.0)], GRID)
    assert [(n.step, n.steps) for n in notes] == [(0, 4), (31, 1)]


def test_result_is_sorted_by_time_then_pitch():
    notes = quantize([_note(1.0, 1.5, 64), _note(0.0, 0.5, 67), _note(0.0, 0.5, 60)], GRID)
    assert [(n.step, n.pitch) for n in notes] == [(0, 60), (0, 67), (8, 64)]


def test_a_double_onset_merges_into_one_note():
    # basic-pitch often starts the same note twice, a 16th apart: 1 step, then the rest.
    [note] = quantize([_note(0.0, 0.1), _note(0.125, 0.5)], GRID)
    assert (note.step, note.steps) == (0, 4)
