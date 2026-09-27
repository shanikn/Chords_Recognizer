"""Notes transcription: the notes of a song's main instrument, as a piano roll and MIDI.

Separate from the chord pipeline, which it uses but never changes: pipeline.analyze()
gives the audio, the section and the bar grid; this package adds stem separation
(Demucs), note transcription (basic-pitch), quantization and MIDI. See
docs/superpowers/plans/2026-09-26-notes-transcription.md.
"""
