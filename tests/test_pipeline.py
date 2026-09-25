import json

import pytest

from chordchart.model import Segment
from chordchart.pipeline import analyze


@pytest.mark.slow
def test_click_track_end_to_end(click_track):
    song = analyze(click_track)

    assert song.title == "click_120_4-4"
    assert song.bpm == pytest.approx(120, abs=2)
    assert song.meter == 4
    assert len(song.bars) >= 8
    assert all(bar.chords for bar in song.bars)
    assert set(song.debug) == {"raw", "beat_sync"}
    assert all(isinstance(s, Segment) for s in song.debug["raw"])
    json.loads(song.to_json())


@pytest.mark.slow
def test_section_times_are_absolute(click_track):
    # Click track: 120 BPM 4/4, loud downbeats at 0, 2, 4, ... s. Analyse 4-16 s.
    song = analyze(click_track, start=4.0, end=16.0)

    assert (song.section_start, song.section_end) == (4.0, 16.0)
    assert song.duration == pytest.approx(12.0, abs=0.05)
    assert song.bars[0].start >= 4.0 - 0.05
    for bar in song.bars:
        assert abs(bar.start / 2 - round(bar.start / 2)) < 0.05  # still on the 2 s grid
        assert all(c.time >= 4.0 - 0.05 for c in bar.chords)
    assert song.bars[-1].end == pytest.approx(16.0, abs=0.05)
    assert song.debug["raw"][0].start == pytest.approx(4.0, abs=0.05)
    assert song.debug["raw"][-1].end == pytest.approx(16.0, abs=0.2)


@pytest.mark.slow
def test_early_strum_track_end_to_end(early_strum_track):
    song = analyze(early_strum_track)
    assert [bar.chords[0].symbol for bar in song.bars[:3]] == ["C", "G", "Am"]
