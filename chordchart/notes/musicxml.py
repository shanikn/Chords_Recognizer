"""The Transcription as sheet music: MusicXML 4.0, a piano grand staff.

Layout: one part, two staves. Notes from middle C (MIDI 60) up go on the treble staff,
lower ones on the bass staff. Time runs on the chart's grid, so one division is one 16th
(`divisions` = steps per beat = 4, beat = quarter note).

Voices: each staff has up to two. A note is *held* if another note on its staff starts
while it sounds (or starts with it and ends sooner): a pedal bass under an arpeggio, a
long top note over a moving line. Held notes go to the second voice (stems down), and
the rest to the first (stems up); a bar with no held notes has one voice and the
renderer picks the stems. The first voice then never overlaps itself, so its notes
are written as they are. Treble staff: voices 1 and 2; bass staff: voices 5 and 6.

Pedal: a held note is written only up to the next note in its voice, the way a pianist
reads a sustained arpeggio, and the sustain pedal (marked under the bass staff) keeps
it ringing to its real end. Overlapping pedal spans are one pedal. The second voice's
rests are hidden. The MIDI file keeps every note's full length (midi.py).

Measures are the chart's bars. A pickup before the first downbeat is an implicit
measure 0; a short last bar (the song stops mid-bar) is filled with rests.

Rhythm: each voice is cut at every note start, note end and barline. What sounds
between two cuts is one chord (or a rest if nothing does), and a note that goes on past
a cut is tied to its continuation: that's how a note across a barline comes out (and,
rarely, two held notes that overlap in the second voice). Lengths that aren't one
written note value (five 16ths, say) are split into tied notes.

Key signature: the chord analysis's key (minor keys use their relative major's
signature); black keys are spelled with sharps in sharp keys and flats in flat keys.
Meter: `meter`/4, as the chart counts beats in quarter notes.
"""

from __future__ import annotations

import bisect
import xml.etree.ElementTree as ET
from itertools import pairwise

from chordchart.notes.midi import PROGRAMS
from chordchart.notes.model import Transcription

MIDDLE_C = 60
VOICES = {1: (1, 2), 2: (5, 6)}  # staff -> (first voice, second voice for held notes)
_DOCTYPE = (
    '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
    '"http://www.musicxml.org/dtds/partwise.dtd">'
)
# Written note values in 16ths, longest first: (length, type, dotted).
_VALUES = [
    (16, "whole", False),
    (12, "half", True),
    (8, "half", False),
    (6, "quarter", True),
    (4, "quarter", False),
    (3, "eighth", True),
    (2, "eighth", False),
    (1, "16th", False),
]
_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
# Major key signature per tonic pitch class, the spelling with fewer accidentals
# (Db rather than C#; F# and Gb tie, F# is used).
_MAJOR_FIFTHS = {0: 0, 1: -5, 2: 2, 3: -3, 4: 4, 5: -1, 6: 6, 7: 1, 8: -4, 9: 3, 10: -2, 11: 5}
_SHARPS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLATS = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]


def key_fifths(tonic: str, mode: str) -> int:
    """Key signature as MusicXML fifths (sharps > 0, flats < 0); 0 if the key is unknown."""
    if not tonic or tonic[0].upper() not in _PITCH_CLASS:
        return 0
    pc = _PITCH_CLASS[tonic[0].upper()] + tonic.count("#") - tonic[1:].count("b")
    if mode == "minor":
        pc += 3  # the relative major shares the signature
    return _MAJOR_FIFTHS[pc % 12]


def to_musicxml(transcription: Transcription) -> bytes:
    t = transcription
    if t.steps_per_beat != 4:
        raise ValueError("MusicXML export expects a 16th-note grid (4 steps per beat)")
    fifths = key_fifths(t.key_tonic, t.key_mode)
    names = _FLATS if fifths < 0 else _SHARPS

    score = ET.Element("score-partwise", version="4.0")
    ET.SubElement(ET.SubElement(score, "work"), "work-title").text = t.title
    encoding = ET.SubElement(ET.SubElement(score, "identification"), "encoding")
    ET.SubElement(encoding, "software").text = "ChordChart"
    part_list = ET.SubElement(score, "part-list")
    score_part = ET.SubElement(part_list, "score-part", id="P1")
    ET.SubElement(score_part, "part-name").text = t.instrument.capitalize()
    ET.SubElement(ET.SubElement(score_part, "score-instrument", id="P1-I1"), "instrument-name")
    score_part[-1][0].text = t.instrument.capitalize()
    midi = ET.SubElement(score_part, "midi-instrument", id="P1-I1")
    ET.SubElement(midi, "midi-program").text = str(PROGRAMS.get(t.instrument, 0) + 1)
    part = ET.SubElement(score, "part", id="P1")

    notes = [(n.step, n.step + n.steps, n.pitch, n.velocity) for n in t.notes if n.steps > 0]
    staves, pedalled = {}, []
    for staff, on_staff in (
        (1, [n for n in notes if n[2] >= MIDDLE_C]),
        (2, [n for n in notes if n[2] < MIDDLE_C]),
    ):
        moving, held = _split_voices(on_staff)
        held, spans = _cut_held(held)
        staves[staff] = (moving, held)
        pedalled += spans
    pedal = _pedal_marks(pedalled)
    measures = _measures(t)
    for index, (start, length, implicit) in enumerate(measures):
        number = index if measures[0][2] else index + 1
        measure = ET.SubElement(part, "measure", number=str(number))
        if implicit:
            measure.set("implicit", "yes")
        if index == 0:
            _attributes(measure, t, fifths)
        end = start + length
        for staff in (1, 2):
            moving, held = staves[staff]
            held_here = [n for n in held if n[0] < end and n[1] > start]
            chunks = [(moving, VOICES[staff][0], "up" if held_here else None)]
            if held_here:
                chunks.append((held_here, VOICES[staff][1], "down"))
            for voice_notes, voice, stem in chunks:
                if len(measure.findall("note")):  # every voice after the first starts over
                    ET.SubElement(ET.SubElement(measure, "backup"), "duration").text = str(length)
                hide_rests = voice == VOICES[staff][1]
                _write_voice(
                    measure, voice_notes, start, end, staff, voice, stem, names, hide_rests
                )
        last = index == len(measures) - 1
        for position, kind in pedal:
            if start <= position < end or (last and position == end):
                _pedal(measure, position - start, length, kind)
        if index == len(measures) - 1:
            barline = ET.SubElement(measure, "barline", location="right")
            ET.SubElement(barline, "bar-style").text = "light-heavy"

    ET.indent(score)
    body = ET.tostring(score, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{_DOCTYPE}\n{body}\n'.encode()


def _measures(t: Transcription) -> list[tuple[int, int, bool]]:
    """(start step, length in steps, implicit) per measure."""
    bar = t.meter * t.steps_per_beat
    starts = list(t.bar_steps) or list(range(0, max(t.steps, 1), bar))
    ends = [*starts[1:], max(t.steps, starts[-1])]
    measures = []
    for i, (a, b) in enumerate(zip(starts, ends, strict=True)):
        length = b - a
        pickup = i == 0 and len(starts) > 1 and 0 < length < bar
        if i == len(starts) - 1:
            length = max(length, bar)  # a short last bar is filled with rests
        measures.append((a, length, pickup))
    return measures


def _attributes(measure: ET.Element, t: Transcription, fifths: int) -> None:
    attributes = ET.SubElement(measure, "attributes")
    ET.SubElement(attributes, "divisions").text = str(t.steps_per_beat)
    key = ET.SubElement(attributes, "key")
    ET.SubElement(key, "fifths").text = str(fifths)
    if t.key_mode in ("major", "minor"):
        ET.SubElement(key, "mode").text = t.key_mode
    time = ET.SubElement(attributes, "time")
    ET.SubElement(time, "beats").text = str(t.meter)
    ET.SubElement(time, "beat-type").text = "4"
    ET.SubElement(attributes, "staves").text = "2"
    for number, sign, line in (("1", "G", "2"), ("2", "F", "4")):
        clef = ET.SubElement(attributes, "clef", number=number)
        ET.SubElement(clef, "sign").text = sign
        ET.SubElement(clef, "line").text = line
    direction = ET.SubElement(measure, "direction", placement="above")
    metronome = ET.SubElement(ET.SubElement(direction, "direction-type"), "metronome")
    ET.SubElement(metronome, "beat-unit").text = "quarter"
    ET.SubElement(metronome, "per-minute").text = str(round(t.bpm))
    ET.SubElement(direction, "sound", tempo=str(round(t.bpm)))


def _split_voices(notes: list[tuple]) -> tuple[list[tuple], list[tuple]]:
    """(moving, held) notes of one staff: held ones sound on while another one starts."""
    starts = sorted(n[0] for n in notes)
    first_end: dict[int, int] = {}  # start -> earliest end of the notes starting there
    for start, end, *_ in notes:
        first_end[start] = min(end, first_end.get(start, end))
    moving, held = [], []
    for note in notes:
        start, end = note[0], note[1]
        later = bisect.bisect_right(starts, start)
        overlapped = later < len(starts) and starts[later] < end
        (held if overlapped or first_end[start] < end else moving).append(note)
    return moving, held


def _cut_held(held: list[tuple]) -> tuple[list[tuple], list[tuple[int, int]]]:
    """Cut each held note where the next held note on its staff starts; return the cut
    notes and the spans (start, real end) the pedal has to hold."""
    starts = sorted({n[0] for n in held})
    cut, spans = [], []
    for start, end, pitch, velocity in held:
        later = bisect.bisect_right(starts, start)
        if later < len(starts) and starts[later] < end:
            cut.append((start, starts[later], pitch, velocity))
            spans.append((start, end))
        else:
            cut.append((start, end, pitch, velocity))
    return cut, spans


def _pedal_marks(spans: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """Pedal down/up steps for the spans, overlapping ones merged; sorted, an up before a
    down at the same step (the pedal is lifted and pressed again)."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    marks = [(s, "start") for s, _ in merged] + [(e, "stop") for _, e in merged]
    return sorted(marks, key=lambda m: (m[0], m[1] != "stop"))


def _pedal(measure, offset: int, length: int, kind: str) -> None:
    """A pedal mark under the bass staff, `offset` 16ths into a measure that's been
    written to its end: step back, place it, step forward again."""
    back = length - offset
    if back:
        ET.SubElement(ET.SubElement(measure, "backup"), "duration").text = str(back)
    direction = ET.SubElement(measure, "direction", placement="below")
    ET.SubElement(ET.SubElement(direction, "direction-type"), "pedal", type=kind, line="yes")
    ET.SubElement(direction, "staff").text = "2"
    ET.SubElement(direction, "sound", {"damper-pedal": "yes" if kind == "start" else "no"})
    if back:
        ET.SubElement(ET.SubElement(measure, "forward"), "duration").text = str(back)


def _write_voice(measure, notes, a, b, staff, voice, stem, names, hide_rests=False) -> None:
    """One voice of one measure [a, b): chords and rests between the cuts, tied across."""
    inside = [n for n in notes if n[0] < b and n[1] > a]
    if not inside:
        _note(measure, None, b - a, staff, voice, whole_measure=True, hidden=hide_rests)
        return
    cuts = sorted({a, b} | {max(a, min(b, x)) for s, e, *_ in inside for x in (s, e)})
    for x, y in pairwise(cuts):
        sounding = sorted((n for n in inside if n[0] <= x and n[1] >= y), key=lambda n: n[2])
        pieces = _split(y - x)
        if not sounding:
            for piece in pieces:
                _note(measure, None, piece, staff, voice, hidden=hide_rests)
            continue
        for i, piece in enumerate(pieces):
            for j, (start, end, pitch, velocity) in enumerate(sounding):
                ties = []
                if i > 0 or start < x:
                    ties.append("stop")
                if i < len(pieces) - 1 or end > y:
                    ties.append("start")
                spelled = (names[pitch % 12], pitch // 12 - 1, velocity)
                _note(measure, spelled, piece, staff, voice, stem, chord=j > 0, ties=ties)


def _split(length: int) -> list[int]:
    """`length` 16ths as written note values, longest first (tied when more than one)."""
    pieces = []
    for value, _, _ in _VALUES:
        while length >= value:
            pieces.append(value)
            length -= value
    return pieces


def _note(
    measure,
    spelled,
    length,
    staff,
    voice,
    stem=None,
    chord=False,
    ties=(),
    whole_measure=False,
    hidden=False,
) -> None:
    note = ET.SubElement(measure, "note")
    if hidden:
        note.set("print-object", "no")
    if chord:
        ET.SubElement(note, "chord")
    if spelled is None:
        rest = ET.SubElement(note, "rest")
        if whole_measure:
            rest.set("measure", "yes")
    else:
        name, octave, velocity = spelled
        note.set("dynamics", f"{velocity * 100 / 90:.0f}")  # MusicXML: 100 = forte (90)
        pitch = ET.SubElement(note, "pitch")
        ET.SubElement(pitch, "step").text = name[0]
        if len(name) > 1:
            ET.SubElement(pitch, "alter").text = "1" if name[1] == "#" else "-1"
        ET.SubElement(pitch, "octave").text = str(octave)
    ET.SubElement(note, "duration").text = str(length)
    for kind in ties:
        ET.SubElement(note, "tie", type=kind)
    ET.SubElement(note, "voice").text = str(voice)
    if not whole_measure:
        value = next(v for v in _VALUES if v[0] == length)
        ET.SubElement(note, "type").text = value[1]
        if value[2]:
            ET.SubElement(note, "dot")
    if stem and spelled is not None:
        ET.SubElement(note, "stem").text = stem
    ET.SubElement(note, "staff").text = str(staff)
    if ties:
        notations = ET.SubElement(note, "notations")
        for kind in ties:
            ET.SubElement(notations, "tied", type=kind)
