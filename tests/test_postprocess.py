import pytest

from chordchart.beats import Beats
from chordchart.model import Segment
from chordchart.postprocess import (
    PostprocessOptions,
    beat_sync,
    group_bars,
    labels_to_segments,
    split_points,
)


@pytest.mark.parametrize(
    ("bpb", "points"),
    [
        (2, (0, 1)),
        (3, (0, 2)),
        (4, (0, 2)),
        (5, (0,)),
        (6, (0, 3)),
        (8, (0, 2, 4, 6)),
        (9, (0, 3, 6)),
        (12, (0, 3, 6, 9)),
    ],
)
def test_split_points_follow_the_meter(bpb, points):
    assert split_points(bpb) == points


def test_split_points_can_be_overridden():
    options = PostprocessOptions(split_overrides={4: (0, 1, 2, 3)})
    assert options.split_points_for(4) == (0, 1, 2, 3)
    assert options.split_points_for(3) == (0, 2)


def test_beat_sync_picks_label_with_most_overlap():
    segments = [Segment(0.0, 1.9, "C:maj"), Segment(1.9, 4.0, "G:maj")]
    # Beat [1, 2): C overlaps 0.9 s, G 0.1 s, so C wins.
    assert beat_sync(segments, [0.0, 1.0, 2.0, 3.0], 4.0) == ["C:maj", "C:maj", "G:maj", "G:maj"]


def test_beat_sync_gap_is_no_chord():
    segments = [Segment(0.0, 1.0, "C:maj")]
    assert beat_sync(segments, [0.0, 1.0], 2.0) == ["C:maj", "N"]


def _beats(positions, meter, step=0.5):
    return Beats(
        times=[i * step for i in range(len(positions))],
        positions=positions,
        bpm=60 / step,
        meter=meter,
    )


def test_group_bars_4_4_moves_changes_to_split_points():
    beats = _beats([1, 2, 3, 4, 1, 2, 3, 4], meter=4)
    labels = ["C:maj", "C:maj", "G:maj", "G:maj", "A:min", "A:min", "A:min", "A:min"]

    bars = group_bars(beats, labels, duration=4.0)

    assert [(b.index, b.start, b.end) for b in bars] == [(1, 0.0, 2.0), (2, 2.0, 4.0)]
    assert [(c.beat, c.symbol) for c in bars[0].chords] == [(0, "C"), (2, "G")]
    assert [(c.beat, c.time, c.symbol, c.harte) for c in bars[1].chords] == [
        (0, 2.0, "Am", "A:min")
    ]


def test_group_bars_off_split_change_goes_to_the_majority():
    # Change on beat 1 (0-based) in 4/4: the first half is C, G (a tie, and the earlier
    # beat wins), the second half is G.
    beats = _beats([1, 2, 3, 4], meter=4)
    bars = group_bars(beats, ["C:maj", "G:maj", "G:maj", "G:maj"], duration=2.0)
    assert [(c.beat, c.symbol) for c in bars[0].chords] == [(0, "C"), (2, "G")]


def test_group_bars_same_chord_in_both_halves_is_one_event():
    beats = _beats([1, 2, 3, 4], meter=4)
    bars = group_bars(beats, ["C:maj", "C:maj", "C:maj", "G:maj"], duration=2.0)
    assert [c.symbol for c in bars[0].chords] == ["C"]


def test_group_bars_3_4_splits_at_beat_3():
    beats = _beats([1, 2, 3, 1, 2, 3], meter=3)
    labels = ["C:maj", "C:maj", "G:maj", "A:min", "A:min", "A:min"]
    bars = group_bars(beats, labels, duration=3.0)
    assert [[(c.beat, c.symbol) for c in b.chords] for b in bars] == [
        [(0, "C"), (2, "G")],
        [(0, "Am")],
    ]


def test_group_bars_pickup_becomes_bar_zero():
    beats = _beats([4, 1, 2, 3, 4], meter=4)
    bars = group_bars(beats, ["G:maj"] + ["C:maj"] * 4, duration=2.5)
    assert [b.index for b in bars] == [0, 1]
    assert [(c.beat, c.symbol) for c in bars[0].chords] == [(3, "G")]
    assert bars[1].end == 2.5


def test_group_bars_without_downbeats_is_empty():
    assert group_bars(_beats([2, 3], meter=4), ["C:maj", "C:maj"], duration=1.0) == []


def test_labels_to_segments_merges_repeats():
    assert labels_to_segments([0.0, 1.0, 2.0], ["C:maj", "C:maj", "G:maj"], 3.0) == [
        Segment(0.0, 2.0, "C:maj"),
        Segment(2.0, 3.0, "G:maj"),
    ]
