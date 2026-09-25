import pytest

from chordchart.beats import bpm_from_times, meter_from_positions, track_beats


def test_bpm_uses_median_interval():
    # One outlier interval (a tracking glitch) must not move the tempo.
    times = [0.0, 0.5, 1.0, 1.5, 2.6, 3.1, 3.6]
    assert bpm_from_times(times) == pytest.approx(120.0)


def test_bpm_needs_two_beats():
    assert bpm_from_times([1.0]) == 0.0


def test_meter_is_most_common_complete_bar_length():
    positions = [3, 4, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 1, 2, 3, 4, 1, 2]
    assert meter_from_positions(positions) == 4


def test_meter_without_complete_bar_falls_back_to_max_position():
    assert meter_from_positions([1, 2, 3]) == 3


@pytest.mark.slow
def test_tracks_click_track(click_track):
    beats = track_beats(click_track)

    assert beats.bpm == pytest.approx(120, abs=2)
    assert beats.meter == 4
    downbeats = [t for t, p in zip(beats.times, beats.positions, strict=True) if p == 1]
    assert len(downbeats) >= 8
    # The accented clicks are at 0, 2, 4, ... s. The tracker must find that phase,
    # not just the tempo.
    assert all(abs(t / 2 - round(t / 2)) < 0.05 for t in downbeats)
