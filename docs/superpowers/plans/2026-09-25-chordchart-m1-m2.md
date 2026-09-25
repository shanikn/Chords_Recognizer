# ChordChart Milestones 1–2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `chordchart <audio-file>` CLI that prints a bar-aligned chord chart
with key, tempo and time signature, for a local audio file.

**Architecture:** A Python package `chordchart/` with one module per pipeline stage:
decode (ffmpeg) → beats/downbeats (madmom RNN + DBN) → chords (madmom CNN + CRF behind a
`ChordRecognizer` protocol) → key (madmom CNN) → post-processing (beat-sync, then
bar-quantize at meter-derived split points) → text renderer. `pipeline.analyze()` is the
single entry point and returns a `Song` dataclass, the JSON contract for everything
downstream. The CLI is a thin wrapper around it.

**Tech Stack:** Python 3.12, uv, madmom (pinned git commit), numpy, ffmpeg (system
binary), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-25-chordchart-design.md`

## Global Constraints

- `requires-python = ">=3.12,<3.14"`. Develop on 3.12 (`.python-version`).
- madmom dependency, exactly: `madmom @ git+https://github.com/CPJKU/madmom@27f032e8947204902c675e5e341a3faf5dc86dae`
- Decoded audio format: 44.1 kHz, mono, 16-bit PCM WAV.
- Chord labels inside the pipeline use Harte syntax (`C:maj`, `A:min`, `N`). Display symbols (`C`, `Am`, `N`) appear only in `ChordEvent.symbol` and renderers.
- Audio files are never committed (`.gitignore` already excludes them). Test audio is generated on the fly.
- Test markers: `slow` = runs madmom models; `accuracy` = needs local annotated audio. The default `uv run pytest` excludes both.
- Errors meant for the user subclass `ChordChartError`. The CLI prints `error: <message>` to stderr and exits with the error's `exit_code`. It never shows a traceback for these.
- Text output is ASCII only (Windows consoles redirect to cp1252). Files are written as UTF-8.
- Every module that implements a concept (chroma/CNN features, CRF decoding, beat tracking/DBN, snapping) gets a module docstring explaining it in plain language. The user wants to learn this material. After tasks 4, 5 and 6, explain that concept to the user in conversation too.
- When Claude runs the commands, prefix shell commands with `rtk` (user's global CLAUDE.md), and end commit messages with the Co-Authored-By trailer from the session instructions.

## File Structure

```
pyproject.toml              project metadata, pinned madmom, console script, pytest/ruff config
.python-version             3.12
chordchart/
  __init__.py               package version
  __main__.py               python -m chordchart
  errors.py                 ChordChartError hierarchy
  model.py                  Segment, Key, ChordEvent, Bar, Song, meter_label
  symbols.py                harte_to_symbol
  fetch.py                  decode_to_wav, read_wav (M3 adds URL download + cache here)
  beats.py                  Beats, track_beats, bpm_from_times, meter_from_positions
  key.py                    detect_key
  recognizers/
    __init__.py
    base.py                 ChordRecognizer protocol
    madmom_crf.py           MadmomCRFRecognizer
  postprocess.py            split_points, PostprocessOptions, beat_sync, group_bars, labels_to_segments
  render/
    __init__.py
    text.py                 render_text
  pipeline.py               analyze
  cli.py                    main, build_parser
tests/
  conftest.py               make_audio (ffmpeg lavfi), click_track (numpy), sample_song fixtures
  test_install.py           madmom import + model-load guard (spec §2.1)
  test_model.py, test_symbols.py, test_fetch.py, test_beats.py, test_recognizer_key.py,
  test_postprocess.py, test_render_text.py, test_cli.py, test_pipeline.py
```

**Not in this plan** (later milestones per the spec): URL input and cache (M3),
annotation helper and eval harness (M4), smoothing, merge, simplify, and the
time-based fallback layout (M5). Until M5, "fewer than 2 bars detected" is an error.
Other renderers come in M6.

---

### Task 1: Project setup, pinned madmom, model-load guard

**Files:**
- Create: `pyproject.toml`, `.python-version`, `chordchart/__init__.py`, `tests/__init__.py` (empty), `tests/test_install.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an installable `chordchart` package, `uv run pytest` working, markers `slow` / `accuracy` registered.

- [ ] **Step 1: Install ffmpeg (system binary)**

Run: `winget install --id Gyan.FFmpeg -e`
Then open a **new** terminal (winget changes PATH) and run: `ffmpeg -version`
Expected: a version banner. If the agent's shell still can't find it, ask the user to
restart the session. Don't hardcode an ffmpeg path.

- [ ] **Step 2: Write `pyproject.toml` and `.python-version`**

`.python-version`:
```
3.12
```

`pyproject.toml`:
```toml
[project]
name = "chordchart"
version = "0.1.0"
description = "Detect the chords of a song and print a bar-aligned chord chart."
requires-python = ">=3.12,<3.14"
dependencies = [
    # Pinned: main has not moved since this commit (spec §2.1). PyPI 0.16.1 does not build.
    "madmom @ git+https://github.com/CPJKU/madmom@27f032e8947204902c675e5e341a3faf5dc86dae",
    "numpy>=2.0",
]

[project.scripts]
chordchart = "chordchart.cli:main"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.6", "scipy>=1.13"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.metadata]
allow-direct-references = true

[tool.hatch.build.targets.wheel]
packages = ["chordchart"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = [
    "slow: runs madmom models on generated audio (seconds per test)",
    "accuracy: needs locally stored annotated audio",
]
addopts = "-rs -m 'not slow and not accuracy'"

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

`chordchart/__init__.py`:
```python
"""ChordChart: audio in, bar-aligned chord chart out."""

__version__ = "0.1.0"
```

`tests/__init__.py`: empty file.

- [ ] **Step 3: Install**

Run: `uv sync`
Expected: creates `.venv`, builds madmom from git (Cython + MSVC, about 1–2 minutes), no errors.

- [ ] **Step 4: Write the install / model-load guard test**

`tests/test_install.py`:
```python
"""Guards for the pinned madmom commit (spec §2.1).

madmom's bundled models are pickled with an old NumPy dtype signature. NumPy 2.4+
deprecates it and NumPy 3 is expected to reject it (upstream PR #559). Loading every
processor we use with warnings turned into errors makes that failure show up here
first, instead of as a confusing error deep inside the pipeline.
"""

import warnings


def test_madmom_imports():
    import madmom

    assert madmom.__version__.startswith("0.17")


def test_madmom_models_load_without_warnings():
    from madmom.features.chords import CNNChordFeatureProcessor, CRFChordRecognitionProcessor
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor
    from madmom.features.key import CNNKeyRecognitionProcessor

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        CNNChordFeatureProcessor()
        CRFChordRecognitionProcessor()
        RNNDownBeatProcessor()
        DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=100)
        CNNKeyRecognitionProcessor()
```

- [ ] **Step 5: Run it**

Run: `uv run pytest tests/test_install.py -v`
Expected: 2 passed. (Both tests pass immediately because they guard an environment
rather than drive new code. If `test_madmom_models_load_without_warnings` fails, stop
and report the warning text. Per spec §2.1, the fix is re-pinning to the PR #559 head
commit, and that needs the user's approval.)

- [ ] **Step 6: Lint and commit**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: no findings (run `uv run ruff format .` if needed).

```bash
git add pyproject.toml .python-version uv.lock chordchart/__init__.py tests/__init__.py tests/test_install.py
git commit -m "Set up uv project with pinned madmom and model-load guard"
```

---

### Task 2: Data model and chord symbols

**Files:**
- Create: `chordchart/model.py`, `chordchart/symbols.py`, `tests/test_model.py`, `tests/test_symbols.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Segment(start: float, end: float, label: str)`: frozen; `label` in Harte.
  - `Key(tonic: str, mode: str, confidence: float)`: frozen; `str(key) == "G# minor"`.
  - `ChordEvent(beat: int, time: float, symbol: str, harte: str)`: frozen; `beat` is 0-based within the bar.
  - `Bar(index: int, start: float, end: float, chords: list[ChordEvent])`: index 0 is a pickup bar, 1 is the first full bar.
  - `Song(title, source, duration, key, bpm, meter, bars, warnings=[], debug={})` with `to_json(include_debug: bool = False) -> str`. `debug: dict[str, list[Segment]]`.
  - `meter_label(beats_per_bar: int) -> str`.
  - `harte_to_symbol(harte: str) -> str`: raises `ValueError` for qualities outside maj/min.

- [ ] **Step 1: Write failing tests**

`tests/test_symbols.py`:
```python
import pytest

from chordchart.symbols import harte_to_symbol


@pytest.mark.parametrize(
    ("harte", "symbol"),
    [
        ("C:maj", "C"),
        ("A:min", "Am"),
        ("F#:min", "F#m"),
        ("Bb:maj", "Bb"),
        ("C", "C"),  # Harte shorthand: a bare root means major
        ("N", "N"),
        ("X", "N"),  # "unknown" is shown as no chord
    ],
)
def test_harte_to_symbol(harte, symbol):
    assert harte_to_symbol(harte) == symbol


def test_unsupported_quality_raises():
    with pytest.raises(ValueError, match="C:7"):
        harte_to_symbol("C:7")
```

`tests/test_model.py`:
```python
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
    assert [meter_label(n) for n in (2, 3, 4, 6, 9, 12, 5)] == [
        "2/4", "3/4", "4/4", "6/8", "9/8", "12/8", "5/4",
    ]
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_model.py tests/test_symbols.py -v`
Expected: FAIL / errors with `ModuleNotFoundError: No module named 'chordchart.model'`.

- [ ] **Step 3: Implement**

`chordchart/symbols.py`:
```python
"""Chord symbols: Harte syntax <-> the short form shown on a chart.

Models and annotation files (.lab) write chords in Harte syntax, `root:quality`, e.g.
"C:maj", "A:min", "G:7" (Harte et al., ISMIR 2005). It's unambiguous and it's what
mir_eval scores, so the whole pipeline uses it internally. A chart shows the short
form musicians read: "C", "Am", "G7".

Milestones 1-6 only produce major/minor chords (madmom's vocabulary). Milestone 7
extends this module to 7ths and sus chords.
"""

_QUALITY_SUFFIX = {"maj": "", "min": "m"}


def harte_to_symbol(harte: str) -> str:
    """"A:min" -> "Am". "N" (no chord) and "X" (unknown) both display as "N"."""
    if harte in ("N", "X"):
        return "N"
    root, _, quality = harte.partition(":")
    quality = quality or "maj"
    if quality not in _QUALITY_SUFFIX:
        raise ValueError(f"unsupported chord quality in {harte!r}")
    return root + _QUALITY_SUFFIX[quality]
```

`chordchart/model.py`:
```python
"""The data model every part of ChordChart shares.

`Song` is the contract. The pipeline produces it, and the renderers, the evaluation
harness and (later) the web UI consume it, usually as JSON via `Song.to_json()`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Segment:
    """A chord over a time span, in seconds. `label` is Harte syntax ("A:min", "N")."""

    start: float
    end: float
    label: str


@dataclass(frozen=True)
class Key:
    tonic: str  # "G#"
    mode: str  # "major" | "minor"
    confidence: float  # the model's probability for this key, 0..1

    def __str__(self) -> str:
        return f"{self.tonic} {self.mode}"


@dataclass(frozen=True)
class ChordEvent:
    """A chord change inside a bar."""

    beat: int  # 0-based beat within the bar where the chord starts
    time: float  # seconds
    symbol: str  # display form, "Am"
    harte: str  # "A:min"


@dataclass
class Bar:
    index: int  # 0 = pickup (partial bar before the first downbeat), 1 = first full bar
    start: float
    end: float
    chords: list[ChordEvent]


@dataclass
class Song:
    title: str
    source: str
    duration: float
    key: Key
    bpm: float
    meter: int  # beats per bar
    bars: list[Bar]
    warnings: list[str] = field(default_factory=list)
    # Intermediate chord sequences, per pipeline stage, for the eval harness (spec §7).
    debug: dict[str, list[Segment]] = field(default_factory=dict)

    def to_json(self, include_debug: bool = False) -> str:
        data = asdict(self)
        if not include_debug:
            data.pop("debug")
        return json.dumps(data, indent=2)


_METER_LABELS = {2: "2/4", 3: "3/4", 4: "4/4", 6: "6/8", 9: "9/8", 12: "12/8"}


def meter_label(beats_per_bar: int) -> str:
    return _METER_LABELS.get(beats_per_bar, f"{beats_per_bar}/4")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_model.py tests/test_symbols.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add chordchart/model.py chordchart/symbols.py tests/test_model.py tests/test_symbols.py
git commit -m "Add Song data model and Harte-to-symbol conversion"
```

---

### Task 3: Decode any input to a standard WAV (errors included)

**Files:**
- Create: `chordchart/errors.py`, `chordchart/fetch.py`, `tests/conftest.py`, `tests/test_fetch.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `errors.ChordChartError(Exception)` with class attribute `exit_code = 1`; subclasses `FfmpegNotFoundError()` (no args), `AudioDecodeError(msg)` (exit 2), `AudioRejectedError(msg)` (exit 2).
  - `fetch.SAMPLE_RATE = 44_100`, `fetch.MIN_DURATION = 5.0`, `fetch.DEFAULT_MAX_DURATION = 900.0`.
  - `fetch.decode_to_wav(src: Path, dst: Path, max_duration: float = DEFAULT_MAX_DURATION) -> float` returns the duration in seconds.
  - `fetch.read_wav(path: Path) -> np.ndarray` returns float32 mono in [-1, 1].
  - `tests/conftest.py` fixtures: `make_audio(name, lavfi, seconds, ext="mp3") -> Path`, `click_track -> Path` (20 s, 120 BPM, 4/4, accented downbeats, WAV), `sample_song -> Song`.

- [ ] **Step 1: Write the shared test fixtures**

`tests/conftest.py`:
```python
import shutil
import subprocess

import numpy as np
import pytest
from scipy.io import wavfile

from chordchart.model import Bar, ChordEvent, Key, Song


@pytest.fixture(scope="session")
def make_audio(tmp_path_factory):
    """Generate a test audio file with ffmpeg's built-in signal sources (lavfi)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        # ffmpeg is a required dependency: fail loudly rather than skip.
        pytest.fail("ffmpeg not found on PATH. Install it with: winget install Gyan.FFmpeg")
    folder = tmp_path_factory.mktemp("audio")

    def _make(name: str, lavfi: str, seconds: float, ext: str = "mp3"):
        out = folder / f"{name}.{ext}"
        subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", lavfi,
             "-t", str(seconds), "-ac", "2", str(out)],
            check=True,
        )
        return out

    return _make


def write_click_track(path, seconds=20.0, bpm=120.0, beats_per_bar=4, sr=44_100):
    """Accented click track over a quiet 220 Hz tone. Downbeats are twice as loud,
    which gives the downbeat tracker something to find."""
    t = np.arange(int(seconds * sr)) / sr
    x = 0.1 * np.sin(2 * np.pi * 220 * t)
    window = np.hanning(400)
    for i, start in enumerate(np.arange(0, seconds - 0.01, 60.0 / bpm)):
        k = int(start * sr)
        amp = 1.0 if i % beats_per_bar == 0 else 0.5
        chunk = x[k : k + len(window)]
        chunk += amp * window[: len(chunk)]
    x = 0.8 * x / np.max(np.abs(x))
    wavfile.write(path, sr, (x * 32767).astype(np.int16))
    return path


@pytest.fixture(scope="session")
def click_track(tmp_path_factory):
    return write_click_track(tmp_path_factory.mktemp("click") / "click_120_4-4.wav")


@pytest.fixture
def sample_song():
    def bar(index, start, *chords):
        events = [ChordEvent(beat, start + beat * 0.5, sym, harte) for beat, sym, harte in chords]
        return Bar(index=index, start=start, end=start + 2.0, chords=events)

    return Song(
        title="Test Song",
        source="test.wav",
        duration=10.0,
        key=Key("C", "major", 0.8),
        bpm=120.4,
        meter=4,
        bars=[
            bar(1, 0.0, (0, "C", "C:maj")),
            bar(2, 2.0, (0, "C", "C:maj"), (2, "G", "G:maj")),
            bar(3, 4.0, (0, "Am", "A:min")),
            bar(4, 6.0, (0, "F", "F:maj")),
            bar(5, 8.0, (0, "G", "G:maj")),
        ],
    )
```

- [ ] **Step 2: Write failing tests**

`tests/test_fetch.py`:
```python
import wave

import pytest

from chordchart.errors import AudioDecodeError, AudioRejectedError, FfmpegNotFoundError
from chordchart.fetch import SAMPLE_RATE, decode_to_wav, read_wav


def test_decodes_stereo_48k_mp3_to_standard_wav(make_audio, tmp_path):
    src = make_audio("tone", "sine=frequency=440:sample_rate=48000", 6)
    dst = tmp_path / "out.wav"

    duration = decode_to_wav(src, dst)

    assert duration == pytest.approx(6.0, abs=0.1)
    with wave.open(str(dst), "rb") as f:
        assert (f.getnchannels(), f.getframerate(), f.getsampwidth()) == (1, SAMPLE_RATE, 2)
    samples = read_wav(dst)
    assert samples.dtype.name == "float32"
    assert 0.1 < abs(samples).max() <= 1.0


def test_rejects_too_short(make_audio, tmp_path):
    src = make_audio("short", "sine=frequency=440", 3)
    with pytest.raises(AudioRejectedError, match="at least 5 s"):
        decode_to_wav(src, tmp_path / "out.wav")


def test_rejects_silence(make_audio, tmp_path):
    src = make_audio("silence", "anullsrc=r=44100:cl=stereo", 6)
    with pytest.raises(AudioRejectedError, match="silent"):
        decode_to_wav(src, tmp_path / "out.wav")


def test_rejects_too_long(make_audio, tmp_path):
    src = make_audio("long", "sine=frequency=440", 9)
    with pytest.raises(AudioRejectedError, match="max-duration"):
        decode_to_wav(src, tmp_path / "out.wav", max_duration=6.0)


def test_missing_file(tmp_path):
    with pytest.raises(AudioDecodeError, match="not found"):
        decode_to_wav(tmp_path / "nope.mp3", tmp_path / "out.wav")


def test_not_audio(tmp_path):
    src = tmp_path / "fake.mp3"
    src.write_text("this is not audio")
    with pytest.raises(AudioDecodeError, match="could not decode"):
        decode_to_wav(src, tmp_path / "out.wav")


def test_ffmpeg_missing(monkeypatch, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    monkeypatch.setattr("chordchart.fetch.shutil.which", lambda name: None)
    with pytest.raises(FfmpegNotFoundError, match="winget install Gyan.FFmpeg"):
        decode_to_wav(src, tmp_path / "out.wav")
```

- [ ] **Step 3: Run to confirm failure**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: collection error, `No module named 'chordchart.errors'`.

- [ ] **Step 4: Implement**

`chordchart/errors.py`:
```python
"""Errors that are shown to the user as a one-line message, not a traceback."""


class ChordChartError(Exception):
    exit_code = 1


class FfmpegNotFoundError(ChordChartError):
    def __init__(self) -> None:
        super().__init__(
            "ffmpeg was not found on PATH. Install it with: "
            "winget install Gyan.FFmpeg  (then open a new terminal)"
        )


class AudioDecodeError(ChordChartError):
    exit_code = 2


class AudioRejectedError(ChordChartError):
    exit_code = 2
```

`chordchart/fetch.py`:
```python
"""Pipeline step 1: turn whatever the user gave us into one standard WAV.

Every later stage runs madmom models that expect 44.1 kHz audio. Instead of handling
mp3/m4a/webm, stereo and 48 kHz in every stage, we normalise once with ffmpeg: mono,
44 100 Hz, 16-bit PCM WAV. Mono loses nothing we need, because harmony doesn't depend on
where an instrument sits in the stereo field.

Milestone 3 adds URL download (yt-dlp) and a cache in front of this.
"""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

from chordchart.errors import AudioDecodeError, AudioRejectedError, FfmpegNotFoundError

SAMPLE_RATE = 44_100
MIN_DURATION = 5.0
DEFAULT_MAX_DURATION = 15 * 60.0
SILENCE_RMS = 1e-4  # relative to full scale (1.0)


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise FfmpegNotFoundError()
    return path


def decode_to_wav(src: Path, dst: Path, max_duration: float = DEFAULT_MAX_DURATION) -> float:
    """Decode `src` into a standard WAV at `dst` and return its duration in seconds."""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise AudioDecodeError(f"file not found: {src}")
    cmd = [
        find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le",
        # Stop just past the limit, so a 3-hour file isn't decoded only to be rejected.
        "-t", f"{max_duration + 1:.3f}",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AudioDecodeError(f"ffmpeg could not decode {src.name}: {result.stderr.strip()}")

    samples = read_wav(dst)
    duration = len(samples) / SAMPLE_RATE
    if duration > max_duration:
        raise AudioRejectedError(
            f"audio is longer than {max_duration / 60:g} min; "
            "raise --max-duration to analyse it anyway"
        )
    if duration < MIN_DURATION:
        raise AudioRejectedError(
            f"audio is only {duration:.1f} s long; need at least {MIN_DURATION:.0f} s"
        )
    if float(np.sqrt(np.mean(samples**2))) < SILENCE_RMS:
        raise AudioRejectedError("audio is silent")
    return duration


def read_wav(path: Path) -> np.ndarray:
    """Read a mono 16-bit WAV (as written by decode_to_wav) as float32 in [-1, 1]."""
    with wave.open(str(path), "rb") as f:
        frames = f.readframes(f.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add chordchart/errors.py chordchart/fetch.py tests/conftest.py tests/test_fetch.py
git commit -m "Decode any input to 44.1 kHz mono WAV with clear errors"
```

---

### Task 4: Beat and downbeat tracking

**Files:**
- Create: `chordchart/beats.py`, `tests/test_beats.py`

**Interfaces:**
- Consumes: `click_track` fixture (Task 3).
- Produces:
  - `Beats(times: list[float], positions: list[int], bpm: float, meter: int)`: frozen. `positions` is 1-based (1 = downbeat), and `meter` is beats per bar.
  - `track_beats(wav_path: Path, beats_per_bar: Sequence[int] = (3, 4)) -> Beats`
  - `bpm_from_times(times: Sequence[float]) -> float` returns 0.0 for fewer than 2 beats.
  - `meter_from_positions(positions: Sequence[int]) -> int`

- [ ] **Step 1: Write failing tests**

`tests/test_beats.py`:
```python
import pytest

from chordchart.beats import bpm_from_times, meter_from_positions, track_beats


def test_bpm_uses_median_interval():
    # One outlier interval (a tracking glitch) must not move the tempo.
    times = [0.0, 0.5, 1.0, 1.5, 2.6, 3.1, 3.6]
    assert bpm_from_times(times) == pytest.approx(120.0)


def test_bpm_needs_two_beats():
    assert bpm_from_times([1.0]) == 0.0


def test_meter_is_most_common_complete_bar_length():
    positions = [3, 4, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 1, 2, 3, 4, 1, 2]
    assert meter_from_positions(positions) == 4


def test_meter_without_complete_bar_falls_back_to_max_position():
    assert meter_from_positions([1, 2, 3]) == 3


@pytest.mark.slow
def test_tracks_click_track(click_track):
    beats = track_beats(click_track)

    assert beats.bpm == pytest.approx(120, abs=2)
    assert beats.meter == 4
    downbeats = [t for t, p in zip(beats.times, beats.positions) if p == 1]
    assert len(downbeats) >= 8
    # The accented clicks are at 0, 2, 4, ... s. The tracker must find that phase,
    # not just the tempo.
    assert all(abs(t / 2 - round(t / 2)) < 0.05 for t in downbeats)
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_beats.py -v -m "slow or not slow"`
Expected: collection error, `No module named 'chordchart.beats'`.

- [ ] **Step 3: Implement**

`chordchart/beats.py`:
```python
"""Beat and downbeat tracking: where the beats are, and which of them start a bar.

A chart is organised in bars, so before placing any chord we need the song's
"skeleton": the time of every beat, and each beat's position in its bar (1 = the
downbeat).

madmom does this in two stages:

1. **RNNDownBeatProcessor**, a recurrent neural network. It reads the audio as a
   spectrogram (100 frames per second) and outputs two numbers per frame: the
   probability that a beat falls here, and the probability that a downbeat falls here.
   The RNN hears onsets, accents and harmonic changes, but its output is noisy: a
   peak here, a missing beat there.

2. **DBNDownBeatTrackingProcessor**, a dynamic Bayesian network. It models a bar as a
   cycle of N beats (N from `beats_per_bar`) moving at a tempo that can only drift
   slowly. Viterbi decoding picks the single most probable path through
   (tempo, position-in-bar) given the RNN's activations. Because the path must be
   musically plausible, weak or missing beats are filled in at the right spacing, and
   the bar count stays consistent. That's why we get evenly spaced beats and a stable
   meter from noisy evidence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FPS = 100  # frames per second of the RNN activations


@dataclass(frozen=True)
class Beats:
    times: list[float]  # seconds
    positions: list[int]  # 1-based position of each beat in its bar; 1 = downbeat
    bpm: float
    meter: int  # beats per bar


def track_beats(wav_path: Path, beats_per_bar: Sequence[int] = (3, 4)) -> Beats:
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor

    activations = RNNDownBeatProcessor()(str(wav_path))
    tracker = DBNDownBeatTrackingProcessor(beats_per_bar=list(beats_per_bar), fps=FPS)
    tracked = np.asarray(tracker(activations)).reshape(-1, 2)
    times = [float(t) for t in tracked[:, 0]]
    positions = [int(p) for p in tracked[:, 1]]
    return Beats(times, positions, bpm_from_times(times), meter_from_positions(positions))


def bpm_from_times(times: Sequence[float]) -> float:
    """Tempo from the median beat interval. The median ignores the odd glitch."""
    if len(times) < 2:
        return 0.0
    return 60.0 / float(np.median(np.diff(times)))


def meter_from_positions(positions: Sequence[int]) -> int:
    """Most common number of beats between consecutive downbeats."""
    downbeats = [i for i, p in enumerate(positions) if p == 1]
    lengths = [b - a for a, b in zip(downbeats, downbeats[1:])]
    if not lengths:
        return max(positions, default=4)
    return Counter(lengths).most_common(1)[0][0]
```

- [ ] **Step 4: Run tests (fast and slow)**

Run: `uv run pytest tests/test_beats.py -v -m "slow or not slow"`
Expected: 5 passed. If `test_tracks_click_track` fails, print `beats.positions[:12]` and
`beats.times[:12]` and report them. Don't loosen the assertions without the user's
agreement.

- [ ] **Step 5: Commit, then explain**

```bash
git add chordchart/beats.py tests/test_beats.py
git commit -m "Add beat and downbeat tracking with madmom RNN + DBN"
```
Then explain to the user in conversation: onset/beat activations, why a DBN plus
Viterbi beats peak-picking, and what `beats_per_bar=[3, 4]` constrains.

---

### Task 5: Chord recognizer and key detection

**Files:**
- Create: `chordchart/recognizers/__init__.py` (empty), `chordchart/recognizers/base.py`, `chordchart/recognizers/madmom_crf.py`, `chordchart/key.py`, `tests/test_recognizer_key.py`

**Interfaces:**
- Consumes: `Segment`, `Key` (Task 2); `click_track` fixture (Task 3).
- Produces:
  - `ChordRecognizer` protocol: attribute `name: str`, method `recognize(wav_path: Path) -> list[Segment]`.
  - `MadmomCRFRecognizer()` with `name = "madmom-crf"`.
  - `detect_key(wav_path: Path) -> Key`

- [ ] **Step 1: Write failing tests**

`tests/test_recognizer_key.py`:
```python
import pytest

from chordchart.fetch import read_wav
from chordchart.key import detect_key
from chordchart.recognizers.base import ChordRecognizer
from chordchart.recognizers.madmom_crf import MadmomCRFRecognizer

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
    for a, b in zip(segments, segments[1:]):
        assert a.end == pytest.approx(b.start, abs=1e-6)
    assert {s.label for s in segments} <= MADMOM_LABELS


@pytest.mark.slow
def test_detect_key_returns_a_key(click_track):
    key = detect_key(click_track)
    assert key.tonic in ROOTS
    assert key.mode in ("major", "minor")
    assert 0.0 < key.confidence <= 1.0
```

Before implementing, check the exact label spelling madmom uses (sharps vs flats) so
`MADMOM_LABELS` matches it:

Run: `uv run python -c "from madmom.features.chords import CRFChordRecognitionProcessor as P; print(P().labels if hasattr(P(),'labels') else 'no labels attr')"`

If madmom spells roots with flats, change `ROOTS` to match what it prints. Also look at
`madmom/features/chords.py` (`majmin_targets_to_chord_labels`) if the attribute isn't there.

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_recognizer_key.py -v -m "slow or not slow"`
Expected: collection error, `No module named 'chordchart.key'`.

- [ ] **Step 3: Implement**

`chordchart/recognizers/base.py`:
```python
"""The interface every chord recognizer implements.

Keeping recognizers behind this protocol lets milestone 7 add alternatives (e.g.
Chordino running in WSL) and compare them in the eval harness without touching
the rest of the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from chordchart.model import Segment


class ChordRecognizer(Protocol):
    name: str

    def recognize(self, wav_path: Path) -> list[Segment]:
        """Return contiguous chord segments (Harte labels) covering the whole file."""
        ...
```

`chordchart/recognizers/madmom_crf.py`:
```python
"""Default chord recognizer: madmom's deep chroma CNN + CRF (Korzeniowski & Widmer, 2016).

**Chroma.** A chroma vector has 12 numbers, one per pitch class (C, C#, ..., B): how much
energy the audio has in that pitch class, summed over every octave. A C major chord
lights up C, E and G whatever the voicing or octave, which makes chroma the classic
feature for chord recognition. Hand-built chroma (a filter bank folded into 12 bins)
also picks up overtones, drums and vocals, which blurs the picture.

**Deep chroma (CNNChordFeatureProcessor).** madmom instead *learns* chroma-like
features with a convolutional network trained on songs with annotated chords. The
network sees a short spectrogram context around each frame and learns to respond to
the harmony while ignoring most of the percussion and melody. Output: one feature
vector per frame (10 per second).

**CRF (CRFChordRecognitionProcessor).** Classifying each frame on its own would flicker
(C, C, Am, C, C...) wherever the evidence is ambiguous. A linear-chain conditional
random field scores whole label *sequences*: per-frame evidence plus a learned cost for
switching chords. Viterbi decoding returns the best sequence overall, so a brief
contradictory frame loses to a stable chord. Vocabulary: 12 major + 12 minor + N (no
chord) = 25 labels.
"""

from __future__ import annotations

from pathlib import Path

from chordchart.model import Segment


class MadmomCRFRecognizer:
    name = "madmom-crf"

    def recognize(self, wav_path: Path) -> list[Segment]:
        from madmom.features.chords import (
            CNNChordFeatureProcessor,
            CRFChordRecognitionProcessor,
        )

        features = CNNChordFeatureProcessor()(str(wav_path))
        rows = CRFChordRecognitionProcessor()(features)
        return [
            Segment(float(start), float(end), str(label))
            for start, end, label in zip(rows["start"], rows["end"], rows["label"])
        ]
```

`chordchart/key.py`:
```python
"""Global key detection with madmom's key CNN (Korzeniowski & Widmer, 2018).

The network looks at the whole track and outputs a probability for each of the 24
major/minor keys. We report the most likely one and its probability, so a low
confidence (e.g. a modal or key-changing song) can be flagged to the user.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from chordchart.model import Key


def detect_key(wav_path: Path) -> Key:
    from madmom.features.key import CNNKeyRecognitionProcessor, key_prediction_to_label

    prediction = CNNKeyRecognitionProcessor()(str(wav_path))
    tonic, mode = key_prediction_to_label(prediction).split()  # "G# minor"
    return Key(tonic=tonic, mode=mode, confidence=float(np.max(prediction)))
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_recognizer_key.py -v -m "slow or not slow"`
Expected: 3 passed. (The click track has no real chords, so labels are probably all
`N`. That's fine: this test checks structure, not accuracy.)

- [ ] **Step 5: Commit, then explain**

```bash
git add chordchart/recognizers chordchart/key.py tests/test_recognizer_key.py
git commit -m "Add madmom CNN+CRF chord recognizer and key detection"
```
Then explain to the user: chroma, why a learned chroma is better than a hand-built
one, and why the CRF exists (flicker) even before our own post-processing.

---

### Task 6: Post-processing: beat-sync and meter-aware bar quantization

**Files:**
- Create: `chordchart/postprocess.py`, `tests/test_postprocess.py`

**Interfaces:**
- Consumes: `Segment`, `Bar`, `ChordEvent` (Task 2); `harte_to_symbol` (Task 2); `Beats` (Task 4).
- Produces:
  - `split_points(beats_per_bar: int) -> tuple[int, ...]`
  - `PostprocessOptions(split_overrides: dict[int, tuple[int, ...]] = {})` with `.split_points_for(beats_per_bar) -> tuple[int, ...]`
  - `beat_sync(segments: list[Segment], beat_times: Sequence[float], end_time: float) -> list[str]`: one Harte label per beat.
  - `group_bars(beats: Beats, beat_labels: list[str], duration: float, options: PostprocessOptions | None = None) -> list[Bar]`
  - `labels_to_segments(times: Sequence[float], labels: Sequence[str], end_time: float) -> list[Segment]`

- [ ] **Step 1: Write failing tests**

`tests/test_postprocess.py`:
```python
import pytest

from chordchart.beats import Beats
from chordchart.model import Segment
from chordchart.postprocess import (
    PostprocessOptions,
    beat_sync,
    group_bars,
    labels_to_segments,
    split_points,
)


@pytest.mark.parametrize(
    ("bpb", "points"),
    [
        (2, (0, 1)),
        (3, (0, 2)),
        (4, (0, 2)),
        (5, (0,)),
        (6, (0, 3)),
        (8, (0, 2, 4, 6)),
        (9, (0, 3, 6)),
        (12, (0, 3, 6, 9)),
    ],
)
def test_split_points_follow_the_meter(bpb, points):
    assert split_points(bpb) == points


def test_split_points_can_be_overridden():
    options = PostprocessOptions(split_overrides={4: (0, 1, 2, 3)})
    assert options.split_points_for(4) == (0, 1, 2, 3)
    assert options.split_points_for(3) == (0, 2)


def test_beat_sync_picks_label_with_most_overlap():
    segments = [Segment(0.0, 1.9, "C:maj"), Segment(1.9, 4.0, "G:maj")]
    # Beat [1, 2): C overlaps 0.9 s, G 0.1 s, so C wins.
    assert beat_sync(segments, [0.0, 1.0, 2.0, 3.0], 4.0) == ["C:maj", "C:maj", "G:maj", "G:maj"]


def test_beat_sync_gap_is_no_chord():
    segments = [Segment(0.0, 1.0, "C:maj")]
    assert beat_sync(segments, [0.0, 1.0], 2.0) == ["C:maj", "N"]


def _beats(positions, meter, step=0.5):
    return Beats(times=[i * step for i in range(len(positions))], positions=positions,
                 bpm=60 / step, meter=meter)


def test_group_bars_4_4_moves_changes_to_split_points():
    beats = _beats([1, 2, 3, 4, 1, 2, 3, 4], meter=4)
    labels = ["C:maj", "C:maj", "G:maj", "G:maj", "A:min", "A:min", "A:min", "A:min"]

    bars = group_bars(beats, labels, duration=4.0)

    assert [(b.index, b.start, b.end) for b in bars] == [(1, 0.0, 2.0), (2, 2.0, 4.0)]
    assert [(c.beat, c.symbol) for c in bars[0].chords] == [(0, "C"), (2, "G")]
    assert [(c.beat, c.time, c.symbol, c.harte) for c in bars[1].chords] == [
        (0, 2.0, "Am", "A:min")
    ]


def test_group_bars_off_split_change_goes_to_the_majority():
    # Change on beat 1 (0-based) in 4/4: the first half is C, G (a tie, and the earlier
    # beat wins), the second half is G.
    beats = _beats([1, 2, 3, 4], meter=4)
    bars = group_bars(beats, ["C:maj", "G:maj", "G:maj", "G:maj"], duration=2.0)
    assert [(c.beat, c.symbol) for c in bars[0].chords] == [(0, "C"), (2, "G")]


def test_group_bars_same_chord_in_both_halves_is_one_event():
    beats = _beats([1, 2, 3, 4], meter=4)
    bars = group_bars(beats, ["C:maj", "C:maj", "C:maj", "G:maj"], duration=2.0)
    assert [c.symbol for c in bars[0].chords] == ["C"]


def test_group_bars_3_4_splits_at_beat_3():
    beats = _beats([1, 2, 3, 1, 2, 3], meter=3)
    labels = ["C:maj", "C:maj", "G:maj", "A:min", "A:min", "A:min"]
    bars = group_bars(beats, labels, duration=3.0)
    assert [[(c.beat, c.symbol) for c in b.chords] for b in bars] == [
        [(0, "C"), (2, "G")],
        [(0, "Am")],
    ]


def test_group_bars_pickup_becomes_bar_zero():
    beats = _beats([4, 1, 2, 3, 4], meter=4)
    bars = group_bars(beats, ["G:maj"] + ["C:maj"] * 4, duration=2.5)
    assert [b.index for b in bars] == [0, 1]
    assert [(c.beat, c.symbol) for c in bars[0].chords] == [(3, "G")]
    assert bars[1].end == 2.5


def test_group_bars_without_downbeats_is_empty():
    assert group_bars(_beats([2, 3], meter=4), ["C:maj", "C:maj"], duration=1.0) == []


def test_labels_to_segments_merges_repeats():
    assert labels_to_segments([0.0, 1.0, 2.0], ["C:maj", "C:maj", "G:maj"], 3.0) == [
        Segment(0.0, 2.0, "C:maj"),
        Segment(2.0, 3.0, "G:maj"),
    ]
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_postprocess.py -v`
Expected: collection error, `No module named 'chordchart.postprocess'`.

- [ ] **Step 3: Implement**

`chordchart/postprocess.py`:
```python
"""Turn the recognizer's timed segments into bars of a readable chart.

**Why this step exists.** The model thinks in seconds, but musicians think in bars and
beats. The model puts a chord boundary wherever the audio evidence changes, which is
often a little before or after the beat (a strum that anticipates the bar, a sustained
note that rings over). Occasionally it also puts a short spurious chord mid-bar. Printed
as-is, that gives a chart nobody can play from.

We fix it in two snapping steps:

1. **beat_sync:** give each beat the one chord that covers most of it. Chord changes
   can now only happen on beats.
2. **group_bars:** start a new bar at every downbeat, and inside a bar allow changes
   only at the *split points* the meter suggests (beats 1 and 3 in 4/4, beats 1 and 3
   in 3/4, beats 1 and 4 in 6/8 counted in eighths). Each region between split points
   gets its majority chord.

Snapping costs some accuracy: a real change on beat 2 of a 4/4 bar gets moved. The
eval harness (milestone 4) scores every stage separately to measure that cost.
Smoothing of short chords, merging and vocabulary simplification come in milestone 5.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from chordchart.beats import Beats
from chordchart.model import Bar, ChordEvent, Segment
from chordchart.symbols import harte_to_symbol


def split_points(beats_per_bar: int) -> tuple[int, ...]:
    """0-based beats within a bar where a chord change is allowed (spec §4).

    Compound meters (6, 9, 12 beats) group beats in threes. Simple meters group them
    in twos. 3/4 is the special case (0, 2): the "C . G" waltz split.
    """
    if beats_per_bar <= 2:
        return tuple(range(beats_per_bar))
    if beats_per_bar == 3:
        return (0, 2)
    if beats_per_bar % 3 == 0:
        return tuple(range(0, beats_per_bar, 3))
    if beats_per_bar % 2 == 0:
        return tuple(range(0, beats_per_bar, 2))
    return (0,)


@dataclass(frozen=True)
class PostprocessOptions:
    # beats-per-bar -> split points, replacing split_points() for that meter
    split_overrides: dict[int, tuple[int, ...]] = field(default_factory=dict)

    def split_points_for(self, beats_per_bar: int) -> tuple[int, ...]:
        return self.split_overrides.get(beats_per_bar, split_points(beats_per_bar))


def beat_sync(
    segments: list[Segment], beat_times: Sequence[float], end_time: float
) -> list[str]:
    """Label each beat [t_i, t_i+1) with the segment label that overlaps it most.

    The last beat runs to `end_time`. A beat no segment touches gets "N".
    """
    bounds = [*beat_times, end_time]
    labels = []
    for start, end in zip(bounds, bounds[1:]):
        overlap: dict[str, float] = defaultdict(float)
        for seg in segments:
            amount = min(end, seg.end) - max(start, seg.start)
            if amount > 0:
                overlap[seg.label] += amount
        labels.append(max(overlap, key=overlap.__getitem__) if overlap else "N")
    return labels


def group_bars(
    beats: Beats,
    beat_labels: list[str],
    duration: float,
    options: PostprocessOptions | None = None,
) -> list[Bar]:
    """Group beats into bars (one per downbeat) and choose each bar's chords.

    Beats before the first downbeat form a pickup bar with index 0. Full bars are
    numbered from 1. Returns [] if there is no downbeat at all.
    """
    options = options or PostprocessOptions()
    points = options.split_points_for(beats.meter)
    starts = [i for i, p in enumerate(beats.positions) if p == 1]
    if not starts:
        return []
    first_index = 1
    if starts[0] > 0:
        starts.insert(0, 0)
        first_index = 0

    bars = []
    for n, (a, b) in enumerate(zip(starts, [*starts[1:], len(beats.times)])):
        end = beats.times[b] if b < len(beats.times) else duration
        chords = _quantize_bar(
            beats.times[a:b], beats.positions[a:b], beat_labels[a:b], points
        )
        bars.append(Bar(index=first_index + n, start=beats.times[a], end=end, chords=chords))
    return bars


def _quantize_bar(
    times: Sequence[float],
    positions: Sequence[int],
    labels: Sequence[str],
    points: tuple[int, ...],
) -> list[ChordEvent]:
    # Assign each beat to the last split point at or before it. Positions (not list
    # indices) are used, so a pickup beat "4" lands in the region of beat 3 (0-based).
    regions: dict[int, list[int]] = defaultdict(list)
    for i, pos in enumerate(positions):
        regions[max(p for p in points if p <= pos - 1)].append(i)

    events: list[ChordEvent] = []
    for point in sorted(regions):
        idx = regions[point]
        # most_common is stable, so on a tie the label of the earliest beat wins.
        label = Counter(labels[i] for i in idx).most_common(1)[0][0]
        if events and events[-1].harte == label:
            continue
        first = idx[0]
        events.append(
            ChordEvent(
                beat=positions[first] - 1,
                time=times[first],
                symbol=harte_to_symbol(label),
                harte=label,
            )
        )
    return events


def labels_to_segments(
    times: Sequence[float], labels: Sequence[str], end_time: float
) -> list[Segment]:
    """Per-beat labels -> timed segments, merging repeats (used for debug/eval output)."""
    segments: list[Segment] = []
    for start, stop, label in zip(times, [*times[1:], end_time], labels):
        if segments and segments[-1].label == label:
            segments[-1] = Segment(segments[-1].start, stop, label)
        else:
            segments.append(Segment(start, stop, label))
    return segments
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_postprocess.py -v`
Expected: all pass.

- [ ] **Step 5: Commit, then explain**

```bash
git add chordchart/postprocess.py tests/test_postprocess.py
git commit -m "Add beat-sync and meter-aware bar quantization"
```
Then explain to the user: why raw model boundaries don't make a chart, what each
snapping step trades away, and how split points follow the meter.

---

### Task 7: Plain-text chart renderer

**Files:**
- Create: `chordchart/render/__init__.py` (empty), `chordchart/render/text.py`, `tests/test_render_text.py`

**Interfaces:**
- Consumes: `Song`, `meter_label` (Task 2); `sample_song` fixture (Task 3).
- Produces: `render_text(song: Song) -> str` (ASCII, ends with a newline).

- [ ] **Step 1: Write failing tests (golden output)**

`tests/test_render_text.py`:
```python
from chordchart.render.text import render_text

EXPECTED = (
    "Test Song\n"
    "Key: C major   Tempo: 120 BPM   Time: 4/4\n"
    "\n"
    "| C        | C G      | Am       | F        |\n"
    "| G        |\n"
)


def test_render_text_golden(sample_song):
    assert render_text(sample_song) == EXPECTED


def test_render_text_shows_warnings(sample_song):
    sample_song.warnings.append("2 beats per bar detected")
    lines = render_text(sample_song).splitlines()
    assert lines[2] == "Note: 2 beats per bar detected"


def test_render_text_is_ascii(sample_song):
    render_text(sample_song).encode("ascii")
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_render_text.py -v`
Expected: collection error, `No module named 'chordchart.render'`.

- [ ] **Step 3: Implement**

`chordchart/render/text.py`:
```python
"""Plain-text chord chart: a header, then 4 bars per line.

    Test Song
    Key: C major   Tempo: 120 BPM   Time: 4/4

    | C        | C G      | Am       | F        |
"""

from __future__ import annotations

from chordchart.model import Bar, Song, meter_label

BARS_PER_LINE = 4
CELL_WIDTH = 8


def render_text(song: Song) -> str:
    lines = [
        song.title,
        f"Key: {song.key}   Tempo: {round(song.bpm)} BPM   Time: {meter_label(song.meter)}",
    ]
    lines += [f"Note: {w}" for w in song.warnings]
    lines.append("")
    for i in range(0, len(song.bars), BARS_PER_LINE):
        row = song.bars[i : i + BARS_PER_LINE]
        lines.append("| " + " | ".join(_cell(bar) for bar in row) + " |")
    return "\n".join(lines) + "\n"


def _cell(bar: Bar) -> str:
    return " ".join(c.symbol for c in bar.chords).ljust(CELL_WIDTH)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_render_text.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add chordchart/render tests/test_render_text.py
git commit -m "Add plain-text chord chart renderer"
```

---

### Task 8: Pipeline and CLI, end to end

**Files:**
- Create: `chordchart/pipeline.py`, `chordchart/cli.py`, `chordchart/__main__.py`, `tests/test_cli.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above (`decode_to_wav`, `DEFAULT_MAX_DURATION`, `track_beats`, `MadmomCRFRecognizer`, `ChordRecognizer`, `detect_key`, `beat_sync`, `group_bars`, `labels_to_segments`, `PostprocessOptions`, `render_text`, `Song`, `ChordChartError`, `AudioRejectedError`).
- Produces:
  - `analyze(source: str | Path, *, max_duration: float = DEFAULT_MAX_DURATION, recognizer: ChordRecognizer | None = None, options: PostprocessOptions | None = None, beats_per_bar: Sequence[int] = (3, 4)) -> Song`. `song.debug` has the keys `"raw"` and `"beat_sync"`.
  - `cli.main(argv: list[str] | None = None) -> int`; console script `chordchart`.

- [ ] **Step 1: Write failing tests**

`tests/test_cli.py`:
```python
import json

from chordchart import cli
from chordchart.errors import AudioRejectedError


def test_prints_text_chart(monkeypatch, capsys, sample_song):
    monkeypatch.setattr(cli, "analyze", lambda source, **kw: sample_song)
    assert cli.main(["song.mp3"]) == 0
    assert capsys.readouterr().out.startswith("Test Song\nKey: C major")


def test_json_format(monkeypatch, capsys, sample_song):
    monkeypatch.setattr(cli, "analyze", lambda source, **kw: sample_song)
    assert cli.main(["song.mp3", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Test Song"


def test_output_file(monkeypatch, tmp_path, sample_song):
    monkeypatch.setattr(cli, "analyze", lambda source, **kw: sample_song)
    out = tmp_path / "chart.txt"
    assert cli.main(["song.mp3", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("Test Song")


def test_max_duration_is_passed_in_seconds(monkeypatch, sample_song):
    seen = {}

    def fake_analyze(source, **kw):
        seen.update(kw)
        return sample_song

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    cli.main(["song.mp3", "--max-duration", "20"])
    assert seen["max_duration"] == 1200.0


def test_user_errors_are_one_line(monkeypatch, capsys):
    def boom(source, **kw):
        raise AudioRejectedError("audio is silent")

    monkeypatch.setattr(cli, "analyze", boom)
    assert cli.main(["song.mp3"]) == 2
    err = capsys.readouterr().err
    assert err == "error: audio is silent\n"
```

`tests/test_pipeline.py`:
```python
import json

import pytest

from chordchart.model import Segment
from chordchart.pipeline import analyze


@pytest.mark.slow
def test_click_track_end_to_end(click_track):
    song = analyze(click_track)

    assert song.title == "click_120_4-4"
    assert song.bpm == pytest.approx(120, abs=2)
    assert song.meter == 4
    assert len(song.bars) >= 8
    assert all(bar.chords for bar in song.bars)
    assert set(song.debug) == {"raw", "beat_sync"}
    assert all(isinstance(s, Segment) for s in song.debug["raw"])
    json.loads(song.to_json())
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_cli.py tests/test_pipeline.py -v -m "slow or not slow"`
Expected: collection errors, `cannot import name 'cli'` / `No module named 'chordchart.pipeline'`.

- [ ] **Step 3: Implement**

`chordchart/pipeline.py`:
```python
"""The one public entry point: audio in, `Song` out.

    decode -> beats -> chords -> key -> beat_sync -> group_bars -> Song
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

from chordchart.beats import track_beats
from chordchart.errors import AudioRejectedError
from chordchart.fetch import DEFAULT_MAX_DURATION, decode_to_wav
from chordchart.key import detect_key
from chordchart.model import Song
from chordchart.postprocess import PostprocessOptions, beat_sync, group_bars, labels_to_segments
from chordchart.recognizers.base import ChordRecognizer
from chordchart.recognizers.madmom_crf import MadmomCRFRecognizer

MIN_BARS = 2


def analyze(
    source: str | Path,
    *,
    max_duration: float = DEFAULT_MAX_DURATION,
    recognizer: ChordRecognizer | None = None,
    options: PostprocessOptions | None = None,
    beats_per_bar: Sequence[int] = (3, 4),
) -> Song:
    src = Path(source)
    recognizer = recognizer or MadmomCRFRecognizer()

    with tempfile.TemporaryDirectory(prefix="chordchart-", ignore_cleanup_errors=True) as tmp:
        wav = Path(tmp) / "audio.wav"
        duration = decode_to_wav(src, wav, max_duration)
        beats = track_beats(wav, beats_per_bar)
        segments = recognizer.recognize(wav)
        key = detect_key(wav)

    if sum(p == 1 for p in beats.positions) < MIN_BARS:
        # Milestone 5 replaces this with a time-based fallback layout (spec §8).
        raise AudioRejectedError(
            f"could not find a steady beat (fewer than {MIN_BARS} bars detected)"
        )

    labels = beat_sync(segments, beats.times, duration)
    bars = group_bars(beats, labels, duration, options)

    warnings = []
    if beats.meter == 2:
        warnings.append(
            "2 beats per bar detected: this may be 6/8 counted in dotted quarters."
        )

    return Song(
        title=src.stem,
        source=str(src),
        duration=duration,
        key=key,
        bpm=beats.bpm,
        meter=beats.meter,
        bars=bars,
        warnings=warnings,
        debug={
            "raw": segments,
            "beat_sync": labels_to_segments(beats.times, labels, duration),
        },
    )
```

`chordchart/cli.py`:
```python
"""Command line: chordchart <audio-file> [--format txt|json] [-o FILE]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chordchart.errors import ChordChartError
from chordchart.fetch import DEFAULT_MAX_DURATION
from chordchart.pipeline import analyze
from chordchart.render.text import render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chordchart",
        description="Detect the chords of a song and print a bar-aligned chord chart.",
    )
    parser.add_argument("source", help="path to an audio or video file")
    parser.add_argument("--format", choices=["txt", "json"], default="txt")
    parser.add_argument("-o", "--output", type=Path, help="write to FILE instead of stdout")
    parser.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAX_DURATION / 60,
        metavar="MINUTES",
        help="refuse longer audio (default: %(default)g)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        song = analyze(args.source, max_duration=args.max_duration * 60)
    except ChordChartError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    text = render_text(song) if args.format == "txt" else song.to_json() + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0
```

`chordchart/__main__.py`:
```python
from chordchart.cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Run the whole suite, including slow tests**

Run: `uv run pytest -v -m "slow or not slow"`
Expected: all pass.

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add chordchart/pipeline.py chordchart/cli.py chordchart/__main__.py tests/test_cli.py tests/test_pipeline.py
git commit -m "Wire pipeline and CLI end to end"
```

---

### Task 9: Milestone 2 check on a real clip + README

**Files:**
- Modify: `README` (currently empty)

**Interfaces:**
- Consumes: the `chordchart` console script (Task 8).
- Produces: M2 "done" evidence, and usage docs.

- [ ] **Step 1: Get a real clip from the user**

Ask the user for a 30–60 s clip of a song whose chords they know, saved under
`evaluate/audio/` (gitignored). Don't download anything yourself; URL input is M3.

- [ ] **Step 2: Run the CLI on it**

Run: `uv run chordchart evaluate/audio/<clip>.mp3`
Also run: `uv run chordchart evaluate/audio/<clip>.mp3 --format json -o .cache/<clip>.json`
Expected: a chart with a plausible key, tempo and meter. Show the output to the user
and compare it with the chords they know. Record the observations (what's right, what's
off, e.g. wrong meter, chords one beat early, flicker) in the conversation. They're
the input to M4/M5. Don't tune anything yet.

- [ ] **Step 3: Write the README**

`README`:
```
ChordChart
==========

Detects the chords of a song and prints a bar-aligned chord chart with key, tempo and
time signature. Local use only. The chord and beat models come from madmom (model
files licensed CC BY-NC-SA 4.0, so non-commercial use only).

Setup (Windows)
---------------
    winget install --id Gyan.FFmpeg -e     # then open a new terminal
    uv sync                                # builds madmom from source (needs MSVC Build Tools)

Usage
-----
    uv run chordchart path/to/song.mp3
    uv run chordchart path/to/song.mp3 --format json -o song.json

Tests
-----
    uv run pytest                      # fast tests
    uv run pytest -m "slow or not slow"  # everything except accuracy tests

Design: docs/superpowers/specs/2026-09-25-chordchart-design.md
```

- [ ] **Step 4: Commit**

```bash
git add README
git commit -m "Add README with setup and usage"
```
