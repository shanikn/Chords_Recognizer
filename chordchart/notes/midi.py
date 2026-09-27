"""The Transcription as a standard MIDI file (bytes).

Notes are placed by their grid steps, not their seconds, at one constant tempo (the
song's BPM). So in a DAW or notation program they sit exactly on 16ths and bar 1
starts at the beginning of the file. The price: when the real tempo drifts, the MIDI
drifts from the recording a little. For following along it's the grid that matters.
"""

from __future__ import annotations

import io

import pretty_midi

from chordchart.notes.model import Transcription

# General MIDI programs (0-based) for playback: acoustic grand, steel-string acoustic
# guitar, finger-picked electric bass. "other" is a mix of anything, so piano.
PROGRAMS = {"piano": 0, "guitar": 25, "bass": 33, "other": 0}


def to_midi(transcription: Transcription) -> bytes:
    t = transcription
    midi = pretty_midi.PrettyMIDI(initial_tempo=t.bpm)
    midi.time_signature_changes.append(pretty_midi.TimeSignature(t.meter, 4, 0.0))
    track = pretty_midi.Instrument(program=PROGRAMS.get(t.instrument, 0), name=t.instrument)
    step = 60.0 / t.bpm / t.steps_per_beat
    for note in t.notes:
        track.notes.append(
            pretty_midi.Note(
                velocity=note.velocity,
                pitch=note.pitch,
                start=note.step * step,
                end=(note.step + note.steps) * step,
            )
        )
    midi.instruments.append(track)
    buffer = io.BytesIO()
    midi.write(buffer)
    return buffer.getvalue()
