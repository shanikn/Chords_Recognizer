"""Pipeline step 1: turn whatever the user gave us into one standard WAV.

Every later stage runs madmom models that expect 44.1 kHz audio. Instead of handling
mp3/m4a/webm, stereo and 48 kHz in every stage, we normalise once with ffmpeg: mono,
44 100 Hz, 16-bit PCM WAV. Mono loses nothing we need, because harmony doesn't depend on
where an instrument sits in the stereo field.

Milestone 3 adds URL download (yt-dlp) and a cache in front of this.
"""

from __future__ import annotations

import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from chordchart import bundled
from chordchart.errors import AudioDecodeError, AudioRejectedError, FfmpegNotFoundError
from chordchart.timecode import format_time

SAMPLE_RATE = 44_100
MIN_DURATION = 5.0
DEFAULT_MAX_DURATION = 15 * 60.0
SILENCE_RMS = 1e-4  # relative to full scale (1.0)


def find_ffmpeg() -> str:
    path = bundled.ffmpeg_path()  # the app's own copy when packaged, else PATH
    if path is None:
        raise FfmpegNotFoundError()
    return path


@dataclass(frozen=True)
class Decoded:
    offset: float  # source time of the WAV's first sample
    duration: float  # length of the WAV in seconds, padding included


def decode_to_wav(
    src: Path,
    dst: Path,
    max_duration: float = DEFAULT_MAX_DURATION,
    start: float = 0.0,
    end: float | None = None,
) -> float:
    """Decode `src` (or its `start`-`end` section) to a standard WAV; return its duration."""
    return decode_section(src, dst, start=start, end=end, max_duration=max_duration).duration


def decode_section(
    src: Path,
    dst: Path,
    *,
    start: float = 0.0,
    end: float | None = None,
    pad: float = 0.0,
    max_duration: float = DEFAULT_MAX_DURATION,
) -> Decoded:
    """Decode the `start`-`end` section of `src`, plus up to `pad` seconds on each side.

    ffmpeg seeks in the input, so the rest of the file is never decoded. The padding
    gives the models context at the edges and lets the pipeline widen the section to
    whole bars. The checks (past the end, too short, too long) apply to the section
    that was *requested*, not to the padded audio.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise AudioDecodeError(f"file not found: {src}")
    if end is not None and end <= start:
        raise AudioRejectedError(
            f"--end must be after --start (got {format_time(start)} to {format_time(end)})"
        )
    offset = max(0.0, start - pad)
    stop = None if end is None else end + pad
    samples = _ffmpeg_decode(src, dst, offset, stop, limit=max_duration + 2 * pad + 1)

    duration = len(samples) / SAMPLE_RATE
    audio_end = offset + duration
    requested = min(audio_end, end if end is not None else audio_end) - start
    if requested <= 0 and start > 0:
        raise AudioRejectedError(f"--start {format_time(start)} is past the end of the audio")
    if requested > max_duration:
        raise AudioRejectedError(
            f"audio is longer than {max_duration / 60:g} min; "
            "raise --max-duration to analyse it anyway"
        )
    if requested < MIN_DURATION:
        what = "the selected section" if start > 0 or end is not None else "audio"
        raise AudioRejectedError(
            f"{what} is only {max(requested, 0):.1f} s long; need at least {MIN_DURATION:.0f} s"
        )
    if float(np.sqrt(np.mean(samples**2))) < SILENCE_RMS:
        raise AudioRejectedError("audio is silent")
    return Decoded(offset=offset, duration=duration)


def _ffmpeg_decode(
    src: Path, dst: Path, offset: float, stop: float | None, limit: float
) -> np.ndarray:
    section = []
    if offset > 0:
        section += ["-ss", f"{offset:.3f}"]
    if stop is not None:
        section += ["-to", f"{stop:.3f}"]  # an input option, so an absolute source time
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
        f"{limit:.3f}",
        str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise AudioDecodeError(f"ffmpeg could not decode {src.name}: {result.stderr.strip()}")
    return read_wav(dst)


def read_wav(path: Path) -> np.ndarray:
    """Read a mono 16-bit WAV (as written by decode_to_wav) as float32 in [-1, 1]."""
    return _read_int16(path).astype(np.float32) / 32768.0


def load_signal(path: Path):
    """The WAV as a madmom Signal, in memory, for the models.

    madmom can read the file itself, but it memory-maps it
    (`scipy.io.wavfile.read(mmap=True)`). Processors that are kept and reused (the
    server) hold on to the last input, so the file would stay open and locked on
    Windows. Loading it here gives the models exactly the same int16 samples, and the
    file is closed as soon as this returns. It's also read once instead of three times.
    """
    from madmom.audio.signal import Signal

    return Signal(_read_int16(path), sample_rate=SAMPLE_RATE, num_channels=1)


def model_input(audio):
    """What to hand a madmom processor: a loaded Signal as-is, a path as a string."""
    return str(audio) if isinstance(audio, str | Path) else audio


def _read_int16(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as f:
        frames = f.readframes(f.getnframes())
    return np.frombuffer(frames, dtype="<i2").copy()
