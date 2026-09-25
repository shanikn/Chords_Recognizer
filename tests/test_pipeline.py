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
def test_early_strum_track_end_to_end(early_strum_track):
    song = analyze(early_strum_track)
    assert [bar.chords[0].symbol for bar in song.bars[:3]] == ["C", "G", "Am"]
