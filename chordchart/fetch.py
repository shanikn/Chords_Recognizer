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
from chordchart.timecode import format_time

SAMPLE_RATE = 44_100
MIN_DURATION = 5.0
DEFAULT_MAX_DURATION = 15 * 60.0
SILENCE_RMS = 1e-4  # relative to full scale (1.0)


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise FfmpegNotFoundError()
    return path


def decode_to_wav(
    src: Path,
    dst: Path,
    max_duration: float = DEFAULT_MAX_DURATION,
    start: float = 0.0,
    end: float | None = None,
) -> float:
    """Decode `src` into a standard WAV at `dst` and return its duration in seconds.

    `start`/`end` (seconds in the source) select a section. ffmpeg seeks in the input,
    so the rest of the file is never decoded.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise AudioDecodeError(f"file not found: {src}")
    if end is not None and end <= start:
        raise AudioRejectedError(
            f"--end must be after --start (got {format_time(start)} to {format_time(end)})"
        )
    section = []
    if start > 0:
        section += ["-ss", f"{start:.3f}"]
    if end is not None:
        section += ["-to", f"{end:.3f}"]  # an input option, so an absolute source time
    cmd = [
        find_ffmpeg(),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *section,
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        # Stop just past the limit, so a 3-hour file isn't decoded only to be rejected.
        "-t",
        f"{max_duration + 1:.3f}",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AudioDecodeError(f"ffmpeg could not decode {src.name}: {result.stderr.strip()}")

    samples = read_wav(dst)
    duration = len(samples) / SAMPLE_RATE
    if duration == 0 and start > 0:
        raise AudioRejectedError(f"--start {format_time(start)} is past the end of the audio")
    if duration > max_duration:
        raise AudioRejectedError(
            f"audio is longer than {max_duration / 60:g} min; "
            "raise --max-duration to analyse it anyway"
        )
    if duration < MIN_DURATION:
        what = "the selected section" if start > 0 or end is not None else "audio"
        raise AudioRejectedError(
            f"{what} is only {duration:.1f} s long; need at least {MIN_DURATION:.0f} s"
        )
    if float(np.sqrt(np.mean(samples**2))) < SILENCE_RMS:
        raise AudioRejectedError("audio is silent")
    return duration


def read_wav(path: Path) -> np.ndarray:
    """Read a mono 16-bit WAV (as written by decode_to_wav) as float32 in [-1, 1]."""
    with wave.open(str(path), "rb") as f:
        frames = f.readframes(f.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
