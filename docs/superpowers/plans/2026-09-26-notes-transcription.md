# ChordChart: Notes Transcription — Implementation Plan

**Status: in progress** (branch feat/notes-recognizer).

**Goal:** Besides the chord chart, show the notes of the song's main instrument: a piano
roll under the chord sheet, and a MIDI file to download. It runs only when asked for
(the Notes toggle on the page, or `chordchart notes`), because separating the stems is
slow.

**Architecture:** A new package `chordchart/notes/` that *uses* the chord pipeline and
never changes it. `pipeline.analyze()` supplies the download (and its cache), the
section and the bar grid (`Song.bars`). Then:

    stereo decode of the chart's range -> Demucs htdemucs_6s -> drop vocals and drums
    -> loudest of bass/guitar/piano/other (or the user's choice) -> basic-pitch
    -> quantize to 16ths of the chart's beats, drop short/quiet notes -> MIDI

**Tech stack:** demucs 4.1.0 (torch, CPU), basic-pitch 0.4.0 (its ONNX model through
onnxruntime), pretty_midi (comes with basic-pitch).

## Findings (probed 2026-09-26, 4-core/8-thread Windows machine)

| Probe | Result |
|---|---|
| `basic-pitch` on Windows, Python 3.12 | Not installable as published: requires `tensorflow<2.15.1` (no cp312 build). With tensorflow overridden away it uses onnxruntime. |
| basic-pitch's resampy pin (<0.4.3) | 0.4.2 imports `pkg_resources`, gone from current setuptools. 0.4.3 works. |
| Universal lock | demucs pins numpy<2 on Intel Macs; basic-pitch needs tensorflow-macos on macOS. Lock now covers Windows and Linux only. |
| Demucs htdemucs_6s, 30 s of audio | 56 s with torch's default 4 threads; 90 s with 8 (hyperthreads hurt). About 2x the audio's length. |
| basic-pitch, 30 s stem | 7 s. |
| Weights | demucs 4.1.0 downloads `adefossez/HTDemucs-6s` from the Hugging Face hub. |
| Adele, "Someone Like You", 1:05-1:35 | Stem RMS: vocals 0.17, piano 0.075, the rest < 0.001. Piano chosen, correctly. |

## Decisions

1. **Dependencies** are regular dependencies (the web page and the desktop app both use
   them). `[tool.uv] override-dependencies` removes tensorflow and lifts the resampy pin.
2. **Desktop app** includes the feature. Demucs weights (~80 MB) are downloaded on first
   use into `<cache root>/models`, not bundled. basic-pitch's ONNX model (a few MB)
   ships inside its package.
3. **On demand only.** Chord analysis is unchanged. Notes use the same link and section
   as the chords, so the analysis cache makes the chord part instant.
4. **Grid** comes from `Song.bars`: each bar split into `meter` beats, each beat into 4.
   The piano roll then lines up exactly with the chord chart, and MIDI ticks are on the
   grid (a DAW shows clean notation). MIDI tempo = the song's BPM.
5. **Instrument choice:** the stem with the highest RMS among bass, guitar, piano, other.
   `instrument=` overrides it. All four levels are reported so the page can show them.
6. **Stem cache:** the four kept stems are stored as mono 22.05 kHz FLAC (basic-pitch
   resamples to 22.05 kHz anyway) in `<cache root>/stems/<key>/`, keyed by the stereo
   audio's hash. Switching instrument then reruns only basic-pitch (seconds).
   `chordchart cache info|clear` include them.
7. **Filtering:** drop notes shorter than half a 16th (before snapping) or quieter than
   a minimum amplitude; after snapping, merge overlapping notes of the same pitch.
8. **Web:** `POST /api/notes {source,start,end,instrument}` -> job on the same single
   worker; the job returns `notes` (JSON) and `/api/notes/{job_id}.mid` serves the MIDI.
   The page gets a Chords/Notes toggle; Notes shows an instrument menu (Auto first), a
   Transcribe button, the piano roll (canvas, bar lines + chord names), and Download MIDI.
9. **CLI:** `chordchart notes <source> [--start/--end] [--instrument X] [-o song.mid]`.

## Tasks

1. Dependencies, lock, this plan.
2. `notes/model.py`, `notes/quantize.py` (grid from bars, snap, filter, merge). TDD.
3. `notes/select.py`, `notes/midi.py`. TDD.
4. `notes/stems.py` (stereo decode, weights download, Demucs with progress, stem cache)
   and `notes/transcribe.py` (basic-pitch). Slow tests on generated audio.
5. `notes/pipeline.py` + `chordchart notes` + cache info/clear.
6. Server endpoints + tests with a fake transcriber.
7. Page: toggle, instrument menu, piano roll, Download MIDI. Checked in the browser.
8. Desktop build: spec (hidden imports, basic-pitch model data, exclude lameenc),
   licenses, build and self-test.
9. README.
