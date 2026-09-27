"""Notes as MusicXML: a piano grand staff on the chart's grid (1 division = one 16th)."""

import xml.etree.ElementTree as ET

import pytest

from chordchart.notes.model import Note, Transcription
from chordchart.notes.musicxml import key_fifths, to_musicxml


def _transcription(notes, bar_steps=(0, 16, 32), steps=48, meter=4, key=("A", "major")):
    return Transcription(
        title="Song & Co", source="s", instrument="piano", automatic=True, levels={},
        notes=notes, bpm=96.0, meter=meter, section_start=0.0, section_end=10.0,
        bar_steps=list(bar_steps), key_tonic=key[0], key_mode=key[1], steps=steps,
    )  # fmt: skip


def _note(step, steps, pitch, velocity=80):
    return Note(start=0.0, end=0.0, pitch=pitch, velocity=velocity, step=step, steps=steps)


def _parse(transcription):
    data = to_musicxml(transcription)
    assert data.startswith(b"<?xml")
    assert b"<!DOCTYPE score-partwise" in data
    return ET.fromstring(data)


def _staff_notes(measure, staff):
    return [n for n in measure.findall("note") if n.findtext("staff") == str(staff)]


def _pitch(note):
    p = note.find("pitch")
    alter = p.findtext("alter")
    return p.findtext("step") + {"1": "#", "-1": "b", None: ""}[alter] + p.findtext("octave")


def _ties(note):
    return sorted(t.get("type") for t in note.findall("tie"))


def _voices(measure, staff):
    """{voice: [notes]} of one staff in one measure."""
    voices = {}
    for n in _staff_notes(measure, staff):
        voices.setdefault(n.findtext("voice"), []).append(n)
    return voices


def _staff_durations(measure, staff):
    """The staff's length in the measure; every voice on it must fill exactly that."""
    lengths = {
        voice: sum(int(n.findtext("duration")) for n in notes if n.find("chord") is None)
        for voice, notes in _voices(measure, staff).items()
    }
    assert len(set(lengths.values())) == 1, lengths
    return next(iter(lengths.values()))


def _events(notes):
    return [
        ("rest" if n.find("rest") is not None else _pitch(n), int(n.findtext("duration")), _ties(n))
        for n in notes
        if n.find("chord") is None
    ]


def test_score_structure():
    root = _parse(_transcription([_note(0, 4, 64)]))
    assert root.tag == "score-partwise" and root.get("version") == "4.0"
    assert root.findtext("work/work-title") == "Song & Co"
    [part_info] = root.findall("part-list/score-part")
    [part] = root.findall("part")
    assert part.get("id") == part_info.get("id")
    attributes = part.find("measure/attributes")
    assert attributes.findtext("divisions") == "4"  # a quarter note is 4 sixteenths
    assert attributes.findtext("key/fifths") == "3"  # A major
    assert (attributes.findtext("time/beats"), attributes.findtext("time/beat-type")) == ("4", "4")
    assert attributes.findtext("staves") == "2"
    clefs = {
        c.get("number"): (c.findtext("sign"), c.findtext("line"))
        for c in attributes.findall("clef")
    }
    assert clefs == {"1": ("G", "2"), "2": ("F", "4")}


def test_one_measure_per_bar_and_every_staff_fills_it():
    notes = [_note(0, 4, 64), _note(4, 2, 48), _note(20, 12, 52)]
    root = _parse(_transcription(notes))
    measures = root.findall("part/measure")
    assert [m.get("number") for m in measures] == ["1", "2", "3"]
    for measure in measures:
        for staff in (1, 2):
            assert _staff_durations(measure, staff) == 16
        assert measure.findtext("backup/duration") == "16"


def test_split_at_middle_c():
    root = _parse(_transcription([_note(0, 4, 60), _note(0, 4, 59)]))
    first = root.find("part/measure")
    assert _pitch(_staff_notes(first, 1)[0]) == "C4"
    assert _pitch(_staff_notes(first, 2)[0]) == "B3"


def test_gaps_are_rests_and_an_empty_bar_is_a_measure_rest():
    root = _parse(_transcription([_note(4, 4, 64)]))
    first, second = root.findall("part/measure")[:2]
    staff1 = _staff_notes(first, 1)
    assert [n.find("rest") is not None for n in staff1] == [True, False, True]
    assert [int(n.findtext("duration")) for n in staff1] == [4, 4, 8]
    [rest] = _staff_notes(second, 1)
    assert rest.find("rest").get("measure") == "yes" and rest.findtext("duration") == "16"


def test_a_note_across_a_barline_is_tied():
    root = _parse(_transcription([_note(12, 8, 64)]))  # beat 4 of bar 1 to beat 1 of bar 2
    first, second = root.findall("part/measure")[:2]
    end_of_first = _staff_notes(first, 1)[-1]
    start_of_second = _staff_notes(second, 1)[0]
    assert _pitch(end_of_first) == _pitch(start_of_second) == "E4"
    assert end_of_first.findtext("duration") == start_of_second.findtext("duration") == "4"
    assert _ties(end_of_first) == ["start"] and _ties(start_of_second) == ["stop"]
    assert end_of_first.find("notations/tied").get("type") == "start"


def test_a_held_note_goes_to_a_second_voice_instead_of_being_split():
    # On one staff: E4 held for the whole bar, G4 on beat 2. E4 is one whole note in
    # voice 2 (stems down); the moving line, voice 1 (stems up), rests around G4.
    root = _parse(_transcription([_note(0, 16, 64), _note(4, 4, 67)]))
    voices = _voices(root.find("part/measure"), 1)
    assert set(voices) == {"1", "2"}
    assert _events(voices["1"]) == [("rest", 4, []), ("G4", 4, []), ("rest", 8, [])]
    assert _events(voices["2"]) == [("E4", 16, [])]
    assert voices["2"][0].findtext("type") == "whole"
    assert {n.findtext("stem") for n in voices["1"] if n.find("rest") is None} == {"up"}
    assert voices["2"][0].findtext("stem") == "down"


def test_bass_pedal_under_an_arpeggio():
    # C3 held for the bar while eighths move above it, all on the bass staff.
    arpeggio = [_note(2 * i, 2, pitch) for i, pitch in enumerate([48, 52, 55, 52, 48, 52, 55, 52])]
    root = _parse(_transcription([_note(0, 16, 48), *arpeggio]))
    measure = root.find("part/measure")
    voices = _voices(measure, 2)
    assert set(voices) == {"5", "6"}  # the bass staff's voices
    assert [e[0] for e in _events(voices["5"])] == ["C3", "E3", "G3", "E3", "C3", "E3", "G3", "E3"]
    assert _events(voices["6"]) == [("C3", 16, [])]
    assert not any(_ties(n) for n in _staff_notes(measure, 2))  # nothing split
    assert _staff_durations(measure, 2) == 16


def test_a_held_note_across_a_barline_is_tied_in_its_voice():
    # D5 from beat 3 of bar 1 to beat 2 of bar 2, with a quarter note moving under it.
    root = _parse(_transcription([_note(8, 12, 74), _note(12, 4, 67), _note(16, 4, 69)]))
    first, second = root.findall("part/measure")[:2]
    held_1, held_2 = _voices(first, 1)["2"], _voices(second, 1)["2"]
    assert _events(held_1) == [("rest", 8, []), ("D5", 8, ["start"])]
    assert _events(held_2)[0] == ("D5", 4, ["stop"])
    assert _staff_durations(first, 1) == _staff_durations(second, 1) == 16


def test_a_bar_without_held_notes_has_one_voice_per_staff():
    root = _parse(_transcription([_note(0, 4, 64), _note(4, 4, 67), _note(0, 8, 48)]))
    measure = root.find("part/measure")
    assert set(_voices(measure, 1)) == {"1"} and set(_voices(measure, 2)) == {"5"}
    assert measure.find("note/stem") is None  # one voice: the renderer picks stems
    assert [b.findtext("duration") for b in measure.findall("backup")] == ["16"]


def test_notes_ending_together_that_start_together_stay_one_chord():
    root = _parse(_transcription([_note(0, 8, 64), _note(0, 8, 67), _note(8, 8, 69)]))
    assert set(_voices(root.find("part/measure"), 1)) == {"1"}


def test_notes_starting_together_are_a_chord():
    root = _parse(_transcription([_note(0, 4, 64), _note(0, 4, 67), _note(0, 4, 71)]))
    staff1 = _staff_notes(root.find("part/measure"), 1)[:3]
    assert [n.find("chord") is not None for n in staff1] == [False, True, True]


def test_unwritable_durations_are_split_and_tied():
    root = _parse(_transcription([_note(0, 5, 64)]))  # five 16ths: a quarter tied to a 16th
    first, second = _staff_notes(root.find("part/measure"), 1)[:2]
    assert [first.findtext("type"), second.findtext("type")] == ["quarter", "16th"]
    assert _ties(first) == ["start"] and _ties(second) == ["stop"]


def test_dotted_values():
    root = _parse(_transcription([_note(0, 6, 64), _note(6, 3, 64)]))
    first, second = _staff_notes(root.find("part/measure"), 1)[:2]
    assert (first.findtext("type"), first.find("dot") is not None) == ("quarter", True)
    assert (second.findtext("type"), second.find("dot") is not None) == ("eighth", True)


def test_pickup_is_an_implicit_measure_zero():
    # A 2-beat pickup, then two 4/4 bars.
    root = _parse(_transcription([_note(0, 8, 64)], bar_steps=(0, 8, 24), steps=40))
    measures = root.findall("part/measure")
    assert [m.get("number") for m in measures] == ["0", "1", "2"]
    assert measures[0].get("implicit") == "yes"
    assert _staff_durations(measures[0], 1) == 8
    assert _staff_durations(measures[1], 1) == 16


def test_a_short_last_bar_is_filled_with_rests():
    root = _parse(_transcription([_note(32, 4, 64)], steps=40))  # song ends 2 beats in
    last = root.findall("part/measure")[-1]
    assert _staff_durations(last, 1) == _staff_durations(last, 2) == 16


def test_three_four_time():
    notes = [_note(8, 8, 64)]  # beat 3 of bar 1 to beat 2 of bar 2
    root = _parse(_transcription(notes, bar_steps=(0, 12, 24), steps=36, meter=3))
    measures = root.findall("part/measure")
    assert measures[0].findtext("attributes/time/beats") == "3"
    assert [_staff_durations(m, 1) for m in measures] == [12, 12, 12]
    assert _ties(_staff_notes(measures[1], 1)[0]) == ["stop"]


@pytest.mark.parametrize(
    ("tonic", "mode", "fifths"),
    [("C", "major", 0), ("A", "major", 3), ("A", "minor", 0), ("F#", "minor", 3),
     ("A#", "major", -2), ("Bb", "major", -2), ("G#", "minor", 5), ("D#", "minor", 6),
     ("F", "major", -1), ("", "", 0)],
)  # fmt: skip
def test_key_signature_from_the_chord_analysis(tonic, mode, fifths):
    assert key_fifths(tonic, mode) == fifths


def test_flat_keys_spell_flats():
    root = _parse(_transcription([_note(0, 4, 70), _note(4, 4, 63)], key=("A#", "major")))
    staff1 = [n for n in _staff_notes(root.find("part/measure"), 1) if n.find("rest") is None]
    assert [_pitch(n) for n in staff1] == ["Bb4", "Eb4"]  # not A#4, D#4
    assert root.find("part/measure/attributes/key/fifths").text == "-2"


def test_no_notes_is_still_a_score():
    root = _parse(_transcription([]))
    assert len(root.findall("part/measure")) == 3
