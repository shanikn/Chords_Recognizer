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
        warnings.append("2 beats per bar detected: this may be 6/8 counted in dotted quarters.")

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
