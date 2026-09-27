"""Notes: picking the main instrument's stem, and writing the MIDI file."""

import io

import numpy as np
import pretty_midi
import pytest

from chordchart.notes.midi import PROGRAMS, to_midi
from chordchart.notes.model import Note, Transcription
from chordchart.notes.select import SILENT_DB, choose, stem_levels


def test_levels_are_rms_in_dbfs():
    t = np.arange(44100) / 44100
    levels = stem_levels({"piano": 0.5 * np.sin(2 * np.pi * 440 * t), "bass": np.zeros(44100)})
    expected = 20 * np.log10(0.5 / np.sqrt(2))
    assert levels["piano"] == pytest.approx(expected, abs=0.05)  # rounded to 0.1 dB
    assert levels["bass"] == SILENT_DB  # a finite floor, so the levels stay valid JSON


def test_the_loudest_stem_is_chosen():
    assert choose({"bass": -30.0, "guitar": -18.0, "piano": -25.0}) == ("guitar", True)


def test_the_user_can_override_the_choice():
    assert choose({"bass": -30.0, "guitar": -18.0}, "bass") == ("bass", False)


def test_an_unknown_instrument_is_refused():
    with pytest.raises(ValueError, match="bass, guitar, piano, other"):
        choose({"bass": -30.0}, "vocals")


def _transcription(notes, bpm=120.0, meter=3, instrument="bass"):
    return Transcription(
        title="t", source="s", instrument=instrument, automatic=True, levels={},
        notes=notes, bpm=bpm, meter=meter, section_start=10.0, section_end=20.0,
    )  # fmt: skip


def test_midi_notes_sit_on_the_grid_at_the_songs_tempo():
    notes = [
        Note(10.0, 10.5, 40, 90, step=0, steps=4),  # times are ignored: the steps count
        Note(10.6, 10.9, 43, 70, step=5, steps=3),
    ]
    midi = pretty_midi.PrettyMIDI(io.BytesIO(to_midi(_transcription(notes, bpm=100.0))))
    [track] = midi.instruments
    sixteenth = 60 / 100 / 4
    assert [(n.pitch, n.velocity) for n in track.notes] == [(40, 90), (43, 70)]
    assert [n.start for n in track.notes] == pytest.approx([0, 5 * sixteenth])
    assert [n.end for n in track.notes] == pytest.approx([4 * sixteenth, 8 * sixteenth])
    assert midi.get_tempo_changes()[1][0] == pytest.approx(100.0)
    ts = midi.time_signature_changes[0]
    assert (ts.numerator, ts.denominator) == (3, 4)
    assert track.program == PROGRAMS["bass"]


def test_midi_of_no_notes_is_still_a_valid_file():
    midi = pretty_midi.PrettyMIDI(io.BytesIO(to_midi(_transcription([]))))
    assert sum(len(i.notes) for i in midi.instruments) == 0
