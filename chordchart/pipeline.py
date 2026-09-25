"""The one public entry point: a file or a link in, `Song` out.

resolve (download) -> decode -> beats -> chords -> key -> beat_sync -> group_bars -> Song
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from chordchart.beats import Beats, track_beats
from chordchart.errors import AudioRejectedError
from chordchart.fetch import DEFAULT_MAX_DURATION, decode_to_wav
from chordchart.key import detect_key
from chordchart.model import Segment, Song
from chordchart.postprocess import PostprocessOptions, beat_sync, group_bars, labels_to_segments
from chordchart.recognizers.base import ChordRecognizer
from chordchart.recognizers.madmom_crf import MadmomCRFRecognizer
from chordchart.sources import resolve_source

MIN_BARS = 2


def analyze(
    source: str | Path,
    *,
    max_duration: float = DEFAULT_MAX_DURATION,
    recognizer: ChordRecognizer | None = None,
    options: PostprocessOptions | None = None,
    beats_per_bar: Sequence[int] = (3, 4),
    start: float = 0.0,
    end: float | None = None,
    refresh: bool = False,
    status: Callable[[str], None] | None = None,
) -> Song:
    """Analyse `source` (a path or an http(s) link), or just its `start`-`end` section.

    The models only see the section, so their times start at 0. They are shifted by
    `start` straight away, and every time in the returned Song is absolute.
    `refresh` re-downloads a cached link. `status` receives one-line progress messages.
    """
    resolved = resolve_source(
        str(source), max_duration=max_duration, refresh=refresh, status=status
    )
    recognizer = recognizer or MadmomCRFRecognizer()

    with tempfile.TemporaryDirectory(prefix="chordchart-", ignore_cleanup_errors=True) as tmp:
        wav = Path(tmp) / "audio.wav"
        duration = decode_to_wav(resolved.path, wav, max_duration, start=start, end=end)
        beats = track_beats(wav, beats_per_bar)
        segments = recognizer.recognize(wav)
        key = detect_key(wav)

    if sum(p == 1 for p in beats.positions) < MIN_BARS:
        # Milestone 5 replaces this with a time-based fallback layout (spec §8).
        raise AudioRejectedError(
            f"could not find a steady beat (fewer than {MIN_BARS} bars detected)"
        )

    beats, segments = _shift(beats, segments, start)
    end_time = start + duration
    labels = beat_sync(segments, beats.times, end_time)
    bars = group_bars(beats, labels, end_time, options)

    warnings = []
    if beats.meter == 2:
        warnings.append("2 beats per bar detected: this may be 6/8 counted in dotted quarters.")

    return Song(
        title=resolved.title,
        source=resolved.source,
        duration=duration,
        key=key,
        bpm=beats.bpm,
        meter=beats.meter,
        bars=bars,
        warnings=warnings,
        section_start=start,
        section_end=end,
        debug={
            "raw": segments,
            "beat_sync": labels_to_segments(beats.times, labels, end_time),
        },
    )


def _shift(beats: Beats, segments: list[Segment], offset: float) -> tuple[Beats, list[Segment]]:
    """Move section-relative times onto the source's timeline."""
    if not offset:
        return beats, segments
    return (
        replace(beats, times=[t + offset for t in beats.times]),
        [Segment(s.start + offset, s.end + offset, s.label) for s in segments],
    )
