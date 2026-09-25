# ChordChart — Design Spec

**Date:** 2026-09-25
**Status:** Approved 2026-09-25 (revised: madmom status, meter-derived splits, stage-wise eval, downbeat marking)

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
| madmom version | `madmom @ git+https://github.com/CPJKU/madmom@27f032e8947204902c675e5e341a3faf5dc86dae` | Pinned commit (2024-08-25, "CI and NumPy compatibility updates"). It is still the head of `main` as of 2026-09-25 (see §2.1). The 2018 PyPI release does not build. Builds from source with Cython, so it needs MSVC Build Tools (present on this machine). |
| Python / tooling | Python 3.12, `uv`, `pytest`, `ruff`; `requires-python = ">=3.12,<3.14"` | 3.12 has the broadest wheel coverage. 3.13 also verified. |
| Download | `yt-dlp` as a Python dependency (upgradable via `uv`); `ffmpeg` as a system binary (`winget install Gyan.FFmpeg`) | yt-dlp breaks often and needs easy upgrades. |
| Evaluation | `mir_eval.chord` | Standard MIREX chord metrics. |
| CLI | `argparse` | No extra dependency needed. |

Rejected for now: Chordino via `chord-extractor` (its `vamp` C++ extension fails
to compile on Windows), essentia (no Windows wheels), autochord and BTC (unmaintained).
Chordino via WSL stays an option for milestone 7 (see §9).

### 2.1 madmom maintenance status (checked 2026-09-25)

GitHub's "pushed 2026-03-20" date does **not** mean `main` has new commits. It
counts pushes to any branch. `main` has been at `27f032e` since 2024-08-25. The
2026 push created a branch with a random name (`QXzxw1ZheKVfoet9`) that points at
that same commit. The only real 2026 activity is **open, unmerged pull requests
from outside contributors**:

- **#559** "Python 3.14 / NumPy 2.4+ compatibility" (May 2026) makes two changes:
  - `setup.py` switches from `distutils` to `setuptools.Extension`.
  - It coerces the int `align` argument to bool when unpickling the bundled models.
    NumPy 2.4+ deprecates the int form, and NumPy 3 is expected to make it an error.
- #558 (CI dependency install) and #548 (replaces deprecated `numpy.math` in
  Cython) touch the same area.

**Nothing newer on `main` to test.** The pin stays at `27f032e`. Verified on that
pin with Python 3.12, NumPy 2.5.3 and setuptools 84:
- the build succeeds;
- every processor we use loads with `-W always` and emits no warnings (chord CNN
  and CRF, downbeat RNN and DBN, key CNN);
- the downbeat tracker gets a synthetic accented 120 BPM click track right.

Guards:
- `requires-python` stays `<3.14`.
- A fast test loads every processor with warnings turned into errors, so a future
  NumPy or setuptools upgrade that hits the #559 issues fails loudly.
- If that happens, the fix is to pin to the PR #559 head commit instead, not to
  fork madmom.

**Inference-time deprecation (found in M2, task 5).** Running the chord CNN makes NumPy
2.5 warn from `madmom/ml/nn/activations.py:148`:

```python
def relu(x, out=None):
    return np.maximum(x, 0, out)   # `out` passed positionally: deprecated in NumPy 2.5
```

- **Affected:** only the chord CNN. The key CNN and the downbeat RNN don't use this
  function. Results are correct today.
- **Risk:** once NumPy turns the deprecation into an error, chord recognition crashes.
  PR #559 does **not** fix this line, and no upstream issue or PR mentions it
  (checked 2026-09-25).
- **Why the load guard missed it:** it only fires when a model *runs*, not when it loads.

Guards:
- **NumPy cap:** `numpy>=2.0,<2.6` in `pyproject.toml`. An upgrade can't silently pull
  in a NumPy that removes the positional form.
- **Test-time warning filter:** pytest `filterwarnings` turns *any* warning raised
  from madmom code into an error, with this one known deprecation as the only
  exception. A new madmom warning, from a NumPy bump or anything else, fails the
  slow tests loudly. Verified both ways: 29 tests pass with the filter, and removing
  the exception fails the chord test.

**Two ways to lift the cap later.** Whichever route is used, delete the
`ignore:Passing more than 2 positional arguments to np.maximum` filter entry at the
same time, so the error filter then proves the fix.
1. **Re-pin to an upstream fix.** When a madmom commit changes the line to
   `np.maximum(x, 0, out=out)` (on `main`, or in the head commit of an open PR if we
   accept that as with #559), update the pinned commit hash. Then run
   `uv run pytest -m "slow or not slow"` with the ignore entry removed. That's the
   preferred route because it leaves no local code.
2. **Apply the one-line fix locally, at runtime.** Add `chordchart/_madmom_compat.py`
   that replaces `madmom.ml.nn.activations.relu` with a version that passes `out=out`
   by keyword. `recognizers/madmom_crf.py` imports it before any processor is built.
   This works because madmom's model files are pickles that refer to `relu` by its
   qualified name, which is looked up when the model loads, so every model picks up
   the replacement. The installed package is never edited. A test asserts that
   `madmom.ml.nn.activations.relu` is the patched function, so the patch can't
   silently stop applying. Remove the module once route 1 becomes available.

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
Bar:   index, start, end, chords: list[ChordEvent]   # at most len(split_points(meter)) events (§4)
ChordEvent: beat (0-based within bar), time, symbol ("Am"), harte ("A:min")
```

Everything downstream (renderers, the eval harness, the future React UI, transpose
and capo) works from `Song`. Transpose and capo are pure functions on symbols.

## 4. Data flow

1. **fetch:** for a URL, yt-dlp downloads bestaudio into the cache. A local path is
   used as-is. ffmpeg decodes to 44.1 kHz mono 16-bit PCM WAV. The cache key is the
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
   3. *bar-quantize:* chord changes may only happen at the bar's **split points**,
      which are derived from the detected beats per bar by `split_points(bpb)`:

      | beats per bar | typical meter | split points (0-based beats) | max chords/bar |
      |---|---|---|---|
      | 2 | 2/4, or 6/8 tracked at the dotted-quarter pulse | {0, 1} | 2 |
      | 3 | 3/4 | {0, 2} (the usual "C . G" waltz split) | 2 |
      | 4 | 4/4 | {0, 2} | 2 |
      | 6, 9, 12 | 6/8, 9/8, 12/8 tracked at the eighth-note pulse | every 3rd beat: {0, 3, …} | bpb / 3 |
      | other | — | every 2nd beat if bpb is even, otherwise {0} | — |

      General rule: bpb divisible by 3 and > 3 means compound meter, grouped in
      threes. Otherwise the meter is simple, grouped in twos, and 3 is the special
      case {0, 2}. A change between split points moves to the nearest one. Each
      segment between split points gets its majority chord. `PostprocessOptions`
      can override the table per bpb. The downbeat tracker's `beats_per_bar`
      candidates (default `[3, 4]`) are an option too, so 2 and 6 can be tried in
      the M5 sweeps.
      Known limit: from the pulse alone, 6/8 at the dotted-quarter pulse can't be
      told apart from 2/4. It is displayed as "2/4" and noted in `Song.warnings`.
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

**Phase 1: beats and downbeats.**
- Set the beats per bar first (default 4), then press play.
- **Two tap keys:**
  - `Enter` = downbeat (beat 1 of a bar).
  - `Space` = any other beat.
- **Bar 1 = your first `Enter`.** Any `Space` taps before it are pickup beats: they
  become a partial **bar 0**, which can hold a chord, and in `beats.txt` they are
  numbered backwards from the end of a bar (a single pickup in 4/4 is beat 4).
  Songs with no pickup simply start with `Enter`.
- **You don't need `Enter` on every bar.** After a downbeat, `Space` taps count up,
  and every *bpb*-th tap automatically becomes the next downbeat. `Enter` forces a
  new bar at that tap. Use it to re-sync when your count drifted, or for a meter
  change. If a bar ends up shorter or longer than *bpb*, it is flagged in yellow in
  the grid, so it's either intentional (e.g. an inserted 2/4 bar) or gets fixed.
- `Backspace` undoes the last tap. Playback speed (0.5×/0.75×/1×) and a global
  latency nudge (±ms) help accuracy.
- **Fixing downbeats later:** in the phase 2 grid each bar shows its beat ticks.
  Alt-clicking a tick makes it a downbeat, which splits the bar there. A bar's
  "merge with next" button removes the following downbeat.
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
- **Scores every stage, not just the final chart**, so you can see what each
  simplification costs:
  - `raw`: the recognizer's segments, straight from the model;
  - then one score after each post-processing step (`beat_sync`, `smooth`,
    `bar_quantize`, `simplify`);
  - `chart`: the final `Song`, converted back to timed segments. Each chord event
    lasts until the next one, using the event times.
  `analyze()` returns these intermediate segment lists in a `debug` field. The
  harness doesn't re-implement the pipeline.
- Reports `mir_eval` **majmin** (the main metric), **root**, and segmentation scores
  for every stage, per song and as a duration-weighted mean. The summary table shows
  each stage's delta from `raw`. It also reports beat and downbeat F-measure against
  `beats.txt`.
- **Tempo "octave" errors are reported separately.** A half- or double-tempo track
  scores about 0.67 beat F-measure, which looks the same as general sloppiness. So the
  harness also reports `mir_eval`'s continuity scores:
  - **CMLt** accepts only the annotated metrical level;
  - **AMLt** also accepts double, half and off-beat tracking.
  A high AMLt with a low CMLt means specifically "octave or phase error". The
  per-song table flags those songs.
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
  of the `chart` stage must be ≥ the recorded baseline − 0.02. This is a regression
  guard.
- *Model load guard (fast):* every madmom processor loads with warnings turned into
  errors (§2.1).

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
   Also in M5: **key-aware enharmonic spelling.** madmom always spells roots with
   sharps (its label table is `A, A#, B, C, C#, ...`), so a song in F major currently
   shows `A#` where a musician expects `Bb`.
   - `symbols.harte_to_symbol` gains a `key: Key | None` parameter.
   - Flat keys spell every root with flats. Flat keys are the major keys F, Bb, Eb,
     Ab, Db and Gb, and the minor keys D, G, C, F, Bb and Eb.
   - All other keys use sharps. With no key, the default is flats for Bb, Eb and Ab
     and sharps for F# and C#, which are the most common spellings in guitar charts.
   - The detected key's own name is respelled by the same rule (`A# major` becomes
     `Bb major`).
   - Only display strings change. Harte labels in the pipeline and in `.lab` files keep
     whatever spelling they have, and `mir_eval` compares pitch classes, so accuracy
     numbers are unaffected. Unit tests cover all 24 keys.
   Also in M5: **half/double-tempo handling.** The downbeat DBN searches 55–215 BPM
   (madmom defaults), so a song and its double or half tempo are often both in range.
   A double-tempo track shows every chord twice, with twice as many bars. A half-tempo
   track silently loses chord changes that happen mid-bar.
   - `--bpm-range MIN-MAX` CLI option, passed to the DBN as `min_bpm`/`max_bpm`, lets
     the user fix a known song. The same range is a sweep parameter in the eval
     harness.
   - A likely-octave-error warning in `Song.warnings`, taken from the chart itself:
     - *probably double:* almost every chord lasts an even number of bars;
     - *probably half:* a large share of bars need both split regions.
     The thresholds are tuned on the annotated songs (CMLt vs AMLt from §7 is the
     ground truth). The warning is kept only if it flags the octave-error songs
     without flagging the correct ones.
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
