import json

from chordchart.model import Bar, ChordEvent, Key, Segment, Song, meter_label


def _song():
    bar = Bar(index=1, start=0.0, end=2.0, chords=[ChordEvent(0, 0.0, "C", "C:maj")])
    return Song(
        title="t",
        source="t.wav",
        duration=2.0,
        key=Key("C", "major", 0.9),
        bpm=120.0,
        meter=4,
        bars=[bar],
        debug={"raw": [Segment(0.0, 2.0, "C:maj")]},
    )


def test_key_str():
    assert str(Key("G#", "minor", 0.5)) == "G# minor"


def test_to_json_excludes_debug_by_default():
    data = json.loads(_song().to_json())
    assert "debug" not in data
    assert data["bars"][0]["chords"][0] == {"beat": 0, "time": 0.0, "symbol": "C", "harte": "C:maj"}
    assert data["key"] == {"tonic": "C", "mode": "major", "confidence": 0.9}


def test_to_json_can_include_debug():
    data = json.loads(_song().to_json(include_debug=True))
    assert data["debug"]["raw"] == [{"start": 0.0, "end": 2.0, "label": "C:maj"}]


def test_meter_label():
    labels = [meter_label(n) for n in (2, 3, 4, 6, 9, 12, 5)]
    assert labels == ["2/4", "3/4", "4/4", "6/8", "9/8", "12/8", "5/4"]
