import json

import pytest

from chordchart.model import Segment
from chordchart.pipeline import analyze
from chordchart.render.text import render_text


@pytest.mark.slow
def test_click_track_end_to_end(click_track):
    events = []
    song = analyze(click_track, status=lambda m, elapsed=None: events.append((m, elapsed)))

    stages = [
        "getting audio",
        "reading audio",
        "tracking beats",
        "recognizing chords",
        "detecting key",
        "building chart",
    ]
    # Each stage: announced, then reported with its time.
    assert [m for m, _ in events] == [s for s in stages for _ in (0, 1)]
    assert all(e is None for _, e in events[0::2])
    assert all(e is not None and e >= 0 for _, e in events[1::2])
    assert list(song.timings) == ["download", "decode", "beats", "chords", "key", "chart"]
    assert song.timings["beats"] > 0
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

    # Already on downbeats, so widening to whole bars changes nothing (within tracking).
    assert (song.requested_start, song.requested_end) == (4.0, 16.0)
    assert song.section_start == pytest.approx(4.0, abs=0.05)
    assert song.section_end == pytest.approx(16.0, abs=0.05)
    assert song.duration == pytest.approx(12.0, abs=0.05)
    assert song.bars[0].start >= 4.0 - 0.05
    for bar in song.bars:
        assert abs(bar.start / 2 - round(bar.start / 2)) < 0.05  # still on the 2 s grid
        assert all(c.time >= 4.0 - 0.05 for c in bar.chords)
    assert song.bars[-1].end == pytest.approx(16.0, abs=0.05)
    assert song.debug["raw"][0].start == pytest.approx(4.0, abs=0.05)
    assert song.debug["raw"][-1].end == pytest.approx(16.0, abs=0.2)


@pytest.mark.slow
def test_mid_bar_section_is_widened_to_whole_bars(click_track):
    # 5 s and 15 s are both beat 3 of a bar (downbeats at 4, 6, ... 14, 16 s).
    song = analyze(click_track, start=5.0, end=15.0)

    assert (song.requested_start, song.requested_end) == (5.0, 15.0)
    assert song.section_start == pytest.approx(4.0, abs=0.05)
    assert song.section_end == pytest.approx(16.0, abs=0.05)
    assert [bar.index for bar in song.bars] == [1, 2, 3, 4, 5, 6]  # no pickup bar 0
    for bar in song.bars:
        assert bar.end - bar.start == pytest.approx(2.0, abs=0.05)  # every bar whole
    assert render_text(song).splitlines()[2] == "Section: 0:04-0:16 (requested 0:05-0:15)"


@pytest.mark.slow
def test_whole_song_still_has_a_pickup_when_it_starts_mid_bar(click_track, tmp_path):
    # Cut the click track so the file itself starts on beat 3: a real anacrusis.
    import subprocess

    cut = tmp_path / "pickup.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            "1.0",
            "-i",
            str(click_track),
            str(cut),
        ],
        check=True,
    )
    song = analyze(cut)
    assert song.bars[0].index == 0
    assert song.requested_start is None


@pytest.mark.slow
def test_early_strum_track_end_to_end(early_strum_track):
    song = analyze(early_strum_track)
    assert [bar.chords[0].symbol for bar in song.bars[:3]] == ["C", "G", "Am"]
