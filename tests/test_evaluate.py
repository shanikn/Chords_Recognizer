"""The evaluation harness's pieces, without downloads or models."""

import numpy as np
import pytest

from chordchart.beats import Beats
from chordchart.model import Segment
from evaluate import align, data, score


def test_the_song_list():
    songs = data.load_songs()
    assert len(songs) == 10
    assert len({s.slug for s in songs}) == 10
    assert all(s.link.startswith("https://www.youtube.com/watch?v=") for s in songs)
    assert songs[0].chord_file.name == "06_-_Let_It_Be.lab"
    assert songs[0].beat_file.parts[-3:] == ("The Beatles", "12_-_Let_It_Be", "06_-_Let_It_Be.txt")


def test_beat_files_with_spaces_or_tabs(tmp_path):
    path = tmp_path / "beats.txt"
    path.write_text("0.5 1\n1.0  2\n1.5\t3\n\n", encoding="utf-8")
    times, positions = data.read_beats(path)
    assert times.tolist() == [0.5, 1.0, 1.5] and positions.tolist() == [1, 2, 3]


def test_chart_segments_run_from_chord_to_chord(sample_song):
    segments = score.chart_segments(sample_song)
    assert segments[:3] == [
        Segment(0.0, 3.0, "C:maj"),  # C in bars 1 and 2 until G on beat 3 of bar 2
        Segment(3.0, 4.0, "G:maj"),
        Segment(4.0, 6.0, "A:min"),
    ]
    assert segments[-1] == Segment(8.0, 10.0, "G:maj")  # the last lasts to the chart's end


def test_identical_chords_score_one():
    reference = [Segment(0.0, 2.0, "C:maj"), Segment(2.0, 4.0, "A:min")]
    intervals, labels = score.segments_to_arrays(reference)
    scores = score.chord_scores(intervals, labels, reference)
    assert scores["majmin"] == pytest.approx(1.0) and scores["root"] == pytest.approx(1.0)


def test_chords_scored_by_duration():
    intervals, labels = np.array([[0.0, 2.0], [2.0, 4.0]]), ["C:maj", "A:min"]
    half_wrong = [Segment(0.0, 2.0, "C:maj"), Segment(2.0, 4.0, "F:maj")]
    assert score.chord_scores(intervals, labels, half_wrong)["majmin"] == pytest.approx(0.5)


def _beats(times, meter=4):
    positions = [i % meter + 1 for i in range(len(times))]
    return Beats(list(times), positions, 120.0, meter)


def test_beats_at_double_tempo_are_flagged_as_a_metrical_level_error():
    reference = np.arange(0.0, 60.0, 0.5)
    positions = [i % 4 + 1 for i in range(len(reference))]
    right = score.beat_scores(reference, positions, _beats(reference))
    assert right["beat_f"] == pytest.approx(1.0) and right["downbeat_f"] == pytest.approx(1.0)
    assert not right["octave"] and (right["meter_ref"], right["meter_est"]) == (4, 4)
    double = score.beat_scores(reference, positions, _beats(np.arange(0.0, 60.0, 0.25)))
    assert double["amlt"] >= 0.8 and double["cmlt"] < 0.5 and double["octave"]


def test_weighted_mean():
    assert score.weighted_mean([1.0, 0.0], [3.0, 1.0]) == pytest.approx(0.75)


def test_reference_shifted_and_clipped_to_the_recording():
    intervals = np.array([[0.0, 0.5], [0.5, 4.0], [4.0, 9.0], [9.0, 10.0]])
    labels = ["N", "C:maj", "G:maj", "N"]
    beats, positions = np.array([0.05, 1.0, 2.0, 9.5]), np.array([1, 2, 3, 4])
    got = score.shift_reference(intervals, labels, beats, positions, offset=-0.2, duration=8.0)
    shifted_intervals, shifted_labels, shifted_beats, shifted_positions = got
    assert np.allclose(shifted_intervals, [[0.0, 0.3], [0.3, 3.8], [3.8, 8.0]])
    assert shifted_labels == ["N", "C:maj", "G:maj"]
    assert np.allclose(shifted_beats, [0.8, 1.8]) and shifted_positions.tolist() == [2, 3]


def _chord_audio(changes, total, sr, beat_period=0.5, beat_start=0.0):
    """Triads (sines with a few harmonics) per (start, root pitch class, minor?) plus
    soft clicks on a steady beat: a beat grid alone can't tell whole-beat shifts apart."""
    t = np.arange(int(total * sr)) / sr
    audio = np.zeros_like(t, dtype=np.float32)
    bounds = [*[c[0] for c in changes[1:]], total]
    for (start, root, minor), end in zip(changes, bounds, strict=True):
        span = (t >= start) & (t < end)
        for interval in (0, 3 if minor else 4, 7):
            f = 220 * 2 ** (((root - 9) % 12 + interval) / 12)
            audio[span] += sum(0.3 / k * np.sin(2 * np.pi * f * k * t[span]) for k in (1, 2, 3))
    for b in np.arange(beat_start, total, beat_period):
        i = int(b * sr)
        audio[i : i + 300] += np.hanning(300)[: len(audio[i : i + 300])]
    return audio / np.abs(audio).max()


def test_offset_from_chords_is_not_fooled_by_whole_beat_shifts():
    # Irregular chord changes; the recording starts 0.9 s after the annotated version.
    sr = align.SR
    names = {0: "C", 7: "G", 9: "A", 5: "F", 2: "D", 4: "E"}
    changes = [(0.0, 0, False), (3.1, 7, False), (4.6, 9, True), (8.3, 5, False),
               (9.8, 2, True), (13.0, 4, False), (15.4, 0, False), (19.7, 7, False)]  # fmt: skip
    offset = 0.9
    audio = _chord_audio(
        [(s + offset, r, m) for s, r, m in changes], 24.0, sr, beat_start=offset % 0.5
    )
    bounds = [*[c[0] for c in changes[1:]], 22.0]
    intervals = np.array([[s, e] for (s, _, _), e in zip(changes, bounds, strict=True)])
    labels = [names[r] + (":min" if m else ":maj") for _, r, m in changes]
    beats = np.arange(0.0, 22.0, 0.5)
    result = align.estimate_offset(audio, beats, intervals, labels)
    assert result.offset == pytest.approx(offset, abs=0.03)
    assert abs(result.coarse - offset) < 0.15
