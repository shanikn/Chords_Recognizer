# ChordChart — Design Spec

**Date:** 2026-09-25
**Status:** Approved in conversation; awaiting written-spec review

## 1. Goal

A local-only tool that takes a YouTube URL or a local audio file and produces a
readable chord chart: chord symbols aligned to bars, with detected key, tempo
and time signature at the top. Exports plain text, ChordPro, and HTML (print to
PDF). A web UI and transpose/capo come later.

**Out of scope:** lyrics transcription, melody, note-level notation, hosting for
other users. The madmom model files are CC BY-NC-SA, so the project stays
non-commercial.

## 2. Key technical decisions

| Decision | Choice | Reason |
|---|---|---|
| Chord recognition | **madmom** CNN chroma + CRF (`CNNChordFeatureProcessor` → `CRFChordRecognitionProcessor`) | Only candidate that installs and runs on Windows + Python 3.12/3.13 (verified 2026-09-25). Output vocabulary: maj/min/N. |
| Beats / downbeats | madmom `RNNDownBeatProcessor` + `DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4])` | Same library. `beat_this` (verified installable) is the fallback if beat accuracy is poor. |
| Key | madmom `CNNKeyRecognitionProcessor` | Same library, 24 major/minor keys. |
| madmom version | `madmom @ git+https://github.com/CPJKU/madmom@27f032e8947204902c675e5e341a3faf5dc86dae` | Pinned commit (2024-08-25, "CI and NumPy compatibility updates"). The 2018 PyPI release does not build. Builds from source with Cython, so it needs MSVC Build Tools (present on this machine). |
| Python / tooling | Python 3.12, `uv`, `pytest`, `ruff`; `requires-python = ">=3.12,<3.14"` | 3.12 has the broadest wheel coverage. 3.13 also verified. |
| Download | `yt-dlp` as a Python dependency (upgradable via `uv`); `ffmpeg` as a system binary (`winget install Gyan.FFmpeg`) | yt-dlp breaks often and needs easy upgrades. |
| Evaluation | `mir_eval.chord` | Standard MIREX chord metrics. |
| CLI | `argparse` | No extra dependency needed. |

Rejected for now: Chordino via `chord-extractor` (its `vamp` C++ extension fails
to compile on Windows), essentia (no Windows wheels), autochord and BTC (unmaintained).
Chordino via WSL stays an option for milestone 7 (see §9).

## 3. Architecture

A Python library at the core. The CLI, the evaluation harness, and later the
web server are thin clients of it.

```
chordchart/
  fetch.py        URL | path -> original file -> 44.1 kHz mono WAV (via ffmpeg)
  cache.py        .cache/<sha256 of original audio>/ : wav, beats.json, raw_chords.json
  beats.py        -> Beats{times[], beat_in_bar[], bpm, meter}
  recognizers/
    base.py       ChordRecognizer protocol: recognize(wav_path) -> list[Segment]
    madmom_crf.py default backend
  key.py          -> Key{tonic, mode, confidence}
  symbols.py      chord symbol parsing: "Am7" <-> Harte "A:min7"; root/quality; simplify()
  postprocess.py  segments + beats -> bars (snap, smooth, merge, simplify)
  model.py        Song, Bar, ChordEvent dataclasses; to_json/from_json
  labio.py        read/write .lab files (start end label)
  render/
    text.py       | C | Am | F  G | G |
    chordpro.py   ChordPro 6 {start_of_grid} ... {end_of_grid}
    html.py       self-contained HTML with print stylesheet (PDF = browser print)
  pipeline.py     analyze(source, options) -> Song   (the one public entry point)
  cli.py          chordchart <url|file> [--format txt|chordpro|html|json] [-o FILE]
  annotate/       .lab annotation helper (§6)
evaluate/
  songs/<slug>/   chords.lab, beats.txt, meta.toml   (committed)
  audio/          <slug>.<ext>                        (gitignored)
  results/        <date>-<gitsha>.json                (committed; accuracy history)
  run_eval.py
tests/
```

### Data model (the JSON contract)

```python
Song:  title, source, duration, key: Key, bpm: float, meter: int,
       bars: list[Bar], warnings: list[str]
Bar:   index, start, end, chords: list[ChordEvent]   # 1–2 events per bar after post-processing
ChordEvent: beat (0-based within bar), time, symbol ("Am"), harte ("A:min")
```

Everything downstream (renderers, the eval harness, the future React UI, transpose
and capo) works from `Song`. Transpose and capo are pure functions on symbols.

## 4. Data flow

1. **fetch:** for a URL, yt-dlp downloads bestaudio into the cache. A local path is
   used as-is. ffmpeg decodes to 44.1 kHz mono float WAV. The cache key is the
   sha256 of the original file, so re-runs skip every stage that already has a
   cached result.
2. **beats:** downbeat tracking gives beat times and each beat's position in the bar.
   BPM = 60 / median inter-beat interval. Meter = the most common bar length.
3. **chords:** the recognizer returns raw `(start, end, harte_label)` segments.
4. **key:** the key recognizer on the full track.
5. **postprocess**, in order. Each step is a pure function with its own tests:
   1. *beat-sync:* each beat gets the label with the most overlap within that beat.
   2. *smooth:* a label that lasts fewer than `min_beats` (default 2) is replaced
      by its stronger neighbour. The model's confidence is not exposed, so
      "stronger" means the longer neighbour, and on a tie the one that is diatonic
      to the detected key.
   3. *bar-quantize:* at most 2 chords per bar, splitting only at the half-bar
      (beat 0 or beat meter/2). A chord change off those beats moves to the nearest
      allowed position.
   4. *merge:* repeated consecutive chords collapse into "same as previous".
   5. *simplify:* map to the display vocabulary (maj/min/N now; 7/sus in M7).
   All thresholds live in one `PostprocessOptions` dataclass so the evaluation
   harness can sweep them.
6. **render** the `Song` into the requested format.

## 5. Output formats

- **Text:** a header (`Title`, `Key: G major · 96 BPM · 4/4`), then 4 bars per line:
  `| G    | Em   | C  D | G    |`.
- **ChordPro:** `{title}`, `{key}`, `{tempo}`, `{time}` directives, then chords in a
  `{start_of_grid}` block (`| G . . . | Em . . . |`).
- **HTML:** a single self-contained file with a bar grid and a print stylesheet.
  "PDF" means the browser's Save-as-PDF, so no WeasyPrint/GTK is needed on Windows.
- **JSON:** the `Song` serialization, used by the eval harness and the future UI.

## 6. Annotation helper (for milestone 4)

**Purpose:** make it quick to hand-annotate 2–3 known songs into `.lab` files.

**Launch:** `chordchart annotate evaluate/audio/<slug>.mp3 --slug <slug>` starts a
stdlib `http.server` on `127.0.0.1` and opens the browser. It serves one static
page (vanilla JS, no build step) plus the audio file.

**Phase 1: beats.**
- Set the meter (default 4/4), press play, and tap `Space` on every beat. The first
  tap is beat 1 of bar 1. `B` marks a downbeat, which re-syncs the bar count
  (for pickups or a meter change). `Backspace` undoes a tap.
- Playback speed (0.5×/0.75×/1×) and a global latency nudge (±ms) help accuracy.
- Optional **"Pre-fill beats from madmom"** button: you then only correct the result.
  Beats may be pre-filled; **chords are never pre-filled**, so the ground truth
  isn't biased toward the model being evaluated.

**Phase 2: chords.**
- A grid of bars, 4 per row, with one text field per bar.
- Accepted input: `Am` (whole bar), `C G` (split evenly), `C . G .` (one token per
  beat, `.` = continue), `N` (no chord). An empty bar means "same as previous".
- Clicking a bar plays from its start. During playback the current bar is highlighted.

**Save:** the page POSTs the raw grid. Python parses it with `symbols.py` (the only
chord parser in the project) and writes `chords.lab` (Harte labels, consecutive
identical chords merged), `beats.txt` (`time beat_in_bar`) and `meta.toml`
(title, artist, source URL, audio filename, duration). Bars that fail to parse come
back as errors and are highlighted. Nothing is written until all bars parse.
Opening an existing slug loads its annotation, so work can resume.

## 7. Evaluation and testing

**Eval harness** (`python -m evaluate.run_eval`):
- Runs `analyze()` on every song whose audio exists locally.
- If the audio duration differs from `meta.toml` by more than 0.5 s, it warns and
  skips that song (it's probably a different upload of the song).
- Reports `mir_eval` **majmin** (the main metric), **root**, and segmentation scores,
  per song and as a duration-weighted mean. It also reports beat F-measure against
  `beats.txt`.
- Writes `evaluate/results/<date>-<gitsha>.json`, so each change has a before/after number.

**pytest suites:**
- *Unit (fast, always run):* symbol parsing and Harte round-trips, `simplify`, each
  post-processing step on hand-built segment lists, `.lab` read/write round-trip,
  annotation grid → `.lab` conversion, renderers against golden files built from a
  fixture `Song`.
- *Smoke (`-m slow`):* the full pipeline on a generated 20 s clip (click track +
  triads). It checks that the output is a structurally valid `Song` and that BPM is
  within ±2 of the click track. It does not check the chords (the model returns N
  on synthetic tones).
- *Accuracy (`-m accuracy`, skipped when audio is missing):* the mean majmin score
  must be ≥ the recorded baseline − 0.02. This is a regression guard.

## 8. Error handling

| Situation | Behaviour |
|---|---|
| `ffmpeg` not on PATH | Exit with the `winget install Gyan.FFmpeg` hint |
| yt-dlp failure (private, geo-blocked, age-gated, network) | Exit code 2, show yt-dlp's message, suggest `uv lock --upgrade-package yt-dlp` |
| Undecodable file | Show ffmpeg's error, exit code 2 |
| Audio < 5 s or near-silent | Clear error |
| Audio > 15 min | Refuse unless `--max-duration` is raised |
| Too few beats found to form bars | Chart falls back to a time-based layout (chords with timestamps) plus a warning in `Song.warnings` |

## 9. Milestones

Each milestone ends with a working, tested state. Concepts are explained in code
comments and in conversation as they come up: chroma and CNN features in M2,
beat tracking and DBN decoding in M2, why smoothing is needed in M5.

1. **Setup:** uv project, pinned madmom, ruff, pytest, ffmpeg installed. *Done when:*
   `uv run pytest` passes an import smoke test.
2. **CLI end-to-end on a local file:** fetch (local), beats, chords, key, minimal
   beat-sync + bar grouping, text renderer. *Done when:* a short real clip produces
   a `| C | Am | ...` chart.
3. **URL input** via yt-dlp, plus the cache. *Done when:* a YouTube URL produces a
   chart, and a second run is served from the cache.
4. **Annotation helper + eval harness:** you annotate 2–3 songs, and the baseline
   majmin is recorded.
5. **Accuracy:** the full post-processing chain, option sweeps in the eval harness,
   and a check of beat_this vs madmom if beat F-measure is weak. *Done when:* every
   kept change is justified by a before/after number.
6. **Exports:** ChordPro and HTML renderers with golden tests.
7. **Extended vocabulary (7/sus):** add the `sevenths` metric to the eval, then try in
   order until one improves `sevenths` without hurting `majmin`:
   (a) template matching for 7/sus on madmom's deep chroma inside segments already
   labelled maj/min;
   (b) **Chordino in WSL** as a second `ChordRecognizer` backend: Ubuntu WSL with
   `chord-extractor` (builds fine on Linux), called via `wsl` subprocess, returning
   JSON segments;
   (c) vendoring the BTC model.
8. **Web UI:** FastAPI (`POST /analyze` with URL or upload → `Song` JSON) + React/Vite
   chart view. This gets a short design pass of its own before implementation.
9. **Stretch:** transpose button, and capo suggestion (the capo position that
   minimises barre chords in the transposed chart).
