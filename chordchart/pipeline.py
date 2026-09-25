"""The one public entry point: a file or a link in, `Song` out.

resolve (download) -> decode -> beats -> chords -> key -> beat_sync -> group_bars -> Song
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from chordchart.beats import Beats, track_beats
from chordchart.errors import AudioRejectedError
from chordchart.fetch import DEFAULT_MAX_DURATION, decode_section
from chordchart.key import detect_key
from chordchart.model import Segment, Song
from chordchart.postprocess import PostprocessOptions, beat_sync, group_bars, labels_to_segments
from chordchart.recognizers.base import ChordRecognizer
from chordchart.recognizers.madmom_crf import MadmomCRFRecognizer
from chordchart.sources import resolve_source

# status(message) or status(label, elapsed=seconds) when a timed stage ends.
Status = Callable[..., None]

MIN_BARS = 2
SECTION_PAD = 5.0  # seconds; at least one 4/4 bar at the DBN's slowest tempo (55 BPM)
BAR_TOLERANCE = 0.1  # a downbeat this close to --start/--end counts as on it


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
    status: Status | None = None,
) -> Song:
    """Analyse `source` (a path or an http(s) link), or just its `start`-`end` section.

    A section is widened to whole bars: the models get SECTION_PAD seconds of extra
    audio on each side, and the chart runs from the last downbeat at or before `start`
    to the first downbeat at or after `end`. So a mid-bar `--start` never produces a
    partial first bar that looks like a pickup. The models' times start at 0; they're
    shifted onto the source's timeline straight away, and every time in the returned
    Song is absolute. `refresh` re-downloads a cached link.

    `status(message)` receives one-line progress messages. Each timed stage is
    announced twice: `status(label)` when it starts and `status(label, elapsed=secs)`
    when it ends. The seconds per stage are also returned in `Song.timings`.
    """
    has_section = start > 0 or end is not None
    timings: dict[str, float] = {}
    stage = _Stages(status, timings)

    with stage("getting audio", "download"):
        resolved = resolve_source(
            str(source), max_duration=max_duration, refresh=refresh, status=status
        )
    recognizer = recognizer or MadmomCRFRecognizer()

    with tempfile.TemporaryDirectory(prefix="chordchart-", ignore_cleanup_errors=True) as tmp:
        wav = Path(tmp) / "audio.wav"
        with stage("reading audio", "decode"):
            decoded = decode_section(
                resolved.path,
                wav,
                start=start,
                end=end,
                pad=SECTION_PAD if has_section else 0.0,
                max_duration=max_duration,
            )
        with stage("tracking beats", "beats"):
            beats = track_beats(wav, beats_per_bar)
        with stage("recognizing chords", "chords"):
            segments = recognizer.recognize(wav)
        with stage("detecting key", "key"):
            key = detect_key(wav)

    with stage("building chart", "chart"):
        beats, segments = _shift(beats, segments, decoded.offset)
        audio_end = decoded.offset + decoded.duration
        chart_start, chart_end = _bar_range(beats, start, end, decoded.offset, audio_end)
        beats, segments = _trim(beats, segments, chart_start, chart_end)

        if sum(p == 1 for p in beats.positions) < MIN_BARS:
            # Milestone 5 replaces this with a time-based fallback layout (spec §8).
            raise AudioRejectedError(
                f"could not find a steady beat (fewer than {MIN_BARS} bars detected)"
            )

        labels = beat_sync(segments, beats.times, chart_end)
        bars = group_bars(beats, labels, chart_end, options)
    end_time = chart_end
    duration = chart_end - chart_start

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
        section_start=chart_start if has_section else 0.0,
        section_end=chart_end if end is not None else None,
        requested_start=start if has_section else None,
        requested_end=end,
        timings=timings,
        debug={
            "raw": segments,
            "beat_sync": labels_to_segments(beats.times, labels, end_time),
        },
    )


class _Stages:
    """`with stage(label, key):` announces the stage, times it, records the time."""

    def __init__(self, status: Status | None, timings: dict[str, float]) -> None:
        self.status = status
        self.timings = timings

    @contextmanager
    def __call__(self, label: str, key: str) -> Iterator[None]:
        if self.status:
            self.status(label)
        began = time.perf_counter()
        yield
        elapsed = time.perf_counter() - began
        self.timings[key] = round(elapsed, 3)
        if self.status:
            self.status(label, elapsed=elapsed)


def _bar_range(
    beats: Beats, start: float, end: float | None, audio_start: float, audio_end: float
) -> tuple[float, float]:
    """The chart's range: from the last downbeat at or before `start` to the first
    downbeat at or after `end`. Without a section, or with no downbeat on that side
    (e.g. `start` within the song's first bar), use the edge of the decoded audio."""
    downbeats = [t for t, p in zip(beats.times, beats.positions, strict=True) if p == 1]
    lo, hi = audio_start, audio_end
    if start > 0:
        before = [t for t in downbeats if t <= start + BAR_TOLERANCE]
        lo = before[-1] if before else audio_start
    if end is not None:
        after = [t for t in downbeats if t >= end - BAR_TOLERANCE]
        hi = after[0] if after else audio_end
    return lo, hi


def _trim(
    beats: Beats, segments: list[Segment], lo: float, hi: float
) -> tuple[Beats, list[Segment]]:
    """Keep beats in [lo, hi) and clip segments to [lo, hi]. The downbeat at `hi`
    starts the next bar, so it's excluded."""
    keep = [i for i, t in enumerate(beats.times) if lo - 1e-6 <= t < hi - 1e-6]
    trimmed = replace(
        beats,
        times=[beats.times[i] for i in keep],
        positions=[beats.positions[i] for i in keep],
    )
    clipped = [
        Segment(max(s.start, lo), min(s.end, hi), s.label)
        for s in segments
        if s.end > lo and s.start < hi
    ]
    return trimmed, clipped


def _shift(beats: Beats, segments: list[Segment], offset: float) -> tuple[Beats, list[Segment]]:
    """Move section-relative times onto the source's timeline."""
    if not offset:
        return beats, segments
    return (
        replace(beats, times=[t + offset for t in beats.times]),
        [Segment(s.start + offset, s.end + offset, s.label) for s in segments],
    )
