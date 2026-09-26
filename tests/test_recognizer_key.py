from itertools import pairwise

import pytest

from chordchart.fetch import read_wav
from chordchart.key import detect_key
from chordchart.recognizers.base import ChordRecognizer
from chordchart.recognizers.madmom_crf import MadmomCRFRecognizer

# madmom spells roots with sharps (majmin_targets_to_chord_labels).
ROOTS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MADMOM_LABELS = {"N"} | {f"{r}:{q}" for r in ROOTS for q in ("maj", "min")}


def test_madmom_recognizer_satisfies_protocol():
    recognizer: ChordRecognizer = MadmomCRFRecognizer()
    assert recognizer.name == "madmom-crf"


@pytest.mark.slow
def test_segments_cover_the_track_contiguously(click_track):
    duration = len(read_wav(click_track)) / 44_100
    segments = MadmomCRFRecognizer().recognize(click_track)

    assert segments[0].start == pytest.approx(0.0, abs=0.01)
    assert segments[-1].end == pytest.approx(duration, abs=0.2)
    for a, b in pairwise(segments):
        assert a.end == pytest.approx(b.start, abs=1e-6)
    assert {s.label for s in segments} <= MADMOM_LABELS


@pytest.mark.slow
def test_detect_key_returns_a_key(click_track):
    key = detect_key(click_track)
    assert key.tonic in ROOTS
    assert key.mode in ("major", "minor")
    assert 0.0 < key.confidence <= 1.0
