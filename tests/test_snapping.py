"""End-to-end check of snapping on a synthesized progression (see postprocess.py).

The track plays | C | G | Am | F  C | twice at 100 BPM, with every chord change
strummed an eighth note early. The model hears the early changes, so its raw segment
boundaries fall *before* the bar lines. beat_sync + group_bars must put them back on
the bar lines.

Not asserted: bar 4's F. The model hears it as Am (F-A-C and A-C-E share two notes).
That's a recognition error, which snapping can't fix and isn't meant to. The eval
harness tracks it.
"""

import pytest

from chordchart.beats import track_beats
from chordchart.fetch import read_wav
from chordchart.postprocess import beat_sync, group_bars, split_points
from chordchart.recognizers.madmom_crf import MadmomCRFRecognizer

BEAT = 0.6  # seconds at 100 BPM


@pytest.fixture(scope="module")
def analysed(early_strum_track):
    duration = len(read_wav(early_strum_track)) / 44_100
    beats = track_beats(early_strum_track)
    segments = MadmomCRFRecognizer().recognize(early_strum_track)
    labels = beat_sync(segments, beats.times, duration)
    return beats, segments, group_bars(beats, labels, duration)


@pytest.mark.slow
def test_tracks_the_pulse(analysed):
    beats, _, bars = analysed
    assert beats.bpm == pytest.approx(100, abs=2)
    assert beats.meter == 4
    assert len(bars) == 8


@pytest.mark.slow
def test_raw_changes_really_are_early(analysed):
    # Guards the premise: if the model's boundaries were already on the bar lines,
    # the snapping assertions below would prove nothing.
    _, segments, _ = analysed
    g_start = next(s.start for s in segments if s.label == "G:maj")
    assert g_start < 4 * BEAT - 0.1


@pytest.mark.slow
def test_changes_snap_onto_bar_lines(analysed):
    _, _, bars = analysed
    first_chord = [bar.chords[0].symbol for bar in bars]
    assert first_chord[:3] == ["C", "G", "Am"]
    assert first_chord[4:7] == ["C", "G", "Am"]
    for bar in bars[:3] + bars[4:7]:
        assert len(bar.chords) == 1
        assert bar.chords[0].beat == 0


@pytest.mark.slow
def test_every_change_is_on_a_split_point(analysed):
    beats, _, bars = analysed
    allowed = set(split_points(beats.meter))
    assert all(c.beat in allowed for bar in bars for c in bar.chords)


@pytest.mark.slow
def test_mid_bar_change_lands_on_beat_3(analysed):
    # Bars 4 and 8 change to C on beat 3 (0-based 2), also strummed early.
    _, _, bars = analysed
    for bar in (bars[3], bars[7]):
        assert (bar.chords[-1].beat, bar.chords[-1].symbol) == (2, "C")
