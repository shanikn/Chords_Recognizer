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
