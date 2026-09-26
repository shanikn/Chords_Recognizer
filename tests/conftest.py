import shutil
import subprocess

import numpy as np
import pytest
from scipy.io import wavfile

from chordchart.model import Bar, ChordEvent, Key, Song


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path_factory):
    """Every test gets its own cache root, so nothing reads or writes the real
    %LOCALAPPDATA%\\chordchart cache (downloads or cached analyses)."""
    root = tmp_path_factory.mktemp("cache")
    monkeypatch.setenv("CHORDCHART_CACHE_DIR", str(root))
    return root


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
            [
                ffmpeg,
                "-nostdin",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                lavfi,
                "-t",
                str(seconds),
                "-ac",
                "2",
                str(out),
            ],
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


# Root-position triads (Hz) in a low guitar register.
TRIADS = {
    "C": (130.8, 164.8, 196.0),
    "G": (98.0, 123.5, 146.8),
    "Am": (110.0, 130.8, 164.8),
    "F": (87.3, 110.0, 130.8),
}
# | C | G | Am | F  C |, played twice. Entries are (chord, start beat, length in beats).
EARLY_STRUM_PROGRESSION = [("C", 0, 4), ("G", 4, 4), ("Am", 8, 4), ("F", 12, 2), ("C", 14, 2)]
EARLY_STRUM_PROGRESSION += [(c, s + 16, d) for c, s, d in EARLY_STRUM_PROGRESSION]


def write_progression(path, progression, bpm=100.0, anticipation=0.5, beats_per_bar=4, sr=44_100):
    """Synthesize chords over an accented pulse.

    Each chord is 5 harmonics per note with a decaying envelope, so it sounds enough
    like an instrument for the model (pure sines come out as "N"). Every chord starts
    `anticipation` beats early, like a player strumming ahead of the beat.
    """
    beat = 60.0 / bpm
    total_beats = max(s + d for _, s, d in progression)
    n = int((total_beats * beat + 1.0) * sr)
    t = np.arange(n) / sr
    x = np.zeros(n)
    for chord, start, length in progression:
        a = max(0.0, (start - anticipation) * beat)
        b = (start + length - anticipation) * beat
        i, j = int(a * sr), int(b * sr)
        tt = t[i:j]
        voice = sum(np.sin(2 * np.pi * f * k * tt) / k for f in TRIADS[chord] for k in range(1, 6))
        x[i:j] += voice * np.exp(-(tt - a) * 0.8)
    click = np.hanning(600)
    for k in range(total_beats):
        i = int(k * beat * sr)
        x[i : i + len(click)] += (6.0 if k % beats_per_bar == 0 else 3.0) * click
    x = 0.6 * x / np.max(np.abs(x))
    wavfile.write(path, sr, (x * 32767).astype(np.int16))
    return path


@pytest.fixture(scope="session")
def early_strum_track(tmp_path_factory):
    return write_progression(
        tmp_path_factory.mktemp("prog") / "early_strum.wav", EARLY_STRUM_PROGRESSION
    )
