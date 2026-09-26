"""The notes entry point: a file or a link in, a `Transcription` out.

    chord analysis (pipeline.analyze: download, section, bars) -> stems (Demucs)
    -> choose the instrument -> notes (basic-pitch) -> quantize to the chart's grid

The chord analysis is the unchanged chord pipeline; its caches make it take seconds
when the chords were just shown. Its bars give the grid, so notes line up with chords.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from chordchart.download import default_cache_dir
from chordchart.fetch import DEFAULT_MAX_DURATION
from chordchart.notes import stems, transcribe
from chordchart.notes.model import Transcription
from chordchart.notes.quantize import STEPS_PER_BEAT, grid_from_bars, nearest_step, quantize
from chordchart.notes.select import choose
from chordchart.pipeline import Status, _Stages
from chordchart.sources import resolve_source

# A stem this quiet (dBFS) is Demucs's leftovers from other instruments, not a part.
# basic-pitch hardly depends on loudness, so it would still "hear" notes in it.
SILENT_STEM_DB = -55.0


def transcribe_notes(
    source: str | Path,
    *,
    start: float = 0.0,
    end: float | None = None,
    instrument: str | None = None,
    max_duration: float = DEFAULT_MAX_DURATION,
    refresh: bool = False,
    status: Status | None = None,
    progress: stems.Progress | None = None,
    analyze_fn: Callable | None = None,
) -> Transcription:
    """Transcribe the main instrument of `source` (or its `start`-`end` section).

    `instrument` (bass/guitar/piano/other) overrides the automatic choice. `refresh`
    separates the stems again instead of using the cached ones. `progress` receives
    the stem separation's fraction done. `analyze_fn` replaces pipeline.analyze (the
    server passes its own, which keeps the chord models loaded).
    """
    if analyze_fn is None:
        from chordchart.pipeline import analyze as analyze_fn
    if instrument is not None:
        choose({}, instrument)  # refuse an unknown name before the slow part
    began = time.perf_counter()
    timings: dict[str, float] = {}
    stage = _Stages(status, timings)

    song = analyze_fn(source, start=start, end=end, max_duration=max_duration, status=status)
    timings.update({f"chords.{k}": v for k, v in song.timings.items()})
    # Served from the download cache (analyze just fetched it): no network.
    resolved = resolve_source(str(source), max_duration=max_duration)

    with stage("separating instruments", "stems"):
        found = stems.separate(
            resolved.path,
            song.section_start,
            song.duration,
            default_cache_dir() / "stems",
            refresh=refresh,
            status=status,
            progress=progress,
        )
    chosen, automatic = choose(found.levels, instrument)
    warnings, raw = [], []
    if found.levels[chosen] < SILENT_STEM_DB:
        level = found.levels[chosen]
        warnings.append(f"There is almost no {chosen} in this song ({level:.0f} dB), so no notes.")
    else:
        with stage(f"transcribing the {chosen}", "notes"):
            raw = transcribe.transcribe(found.path(chosen), chosen, offset=song.section_start)

    grid = grid_from_bars(song.bars, song.meter)
    notes = quantize(raw, grid)
    chords = [
        (nearest_step(grid, event.time), event.symbol) for bar in song.bars for event in bar.chords
    ]
    return Transcription(
        title=song.title,
        source=song.source,
        instrument=chosen,
        automatic=automatic,
        levels=found.levels,
        notes=notes,
        bpm=song.bpm,
        meter=song.meter,
        section_start=grid[0] if grid else song.section_start,
        section_end=grid[-1] if grid else song.section_start + song.duration,
        steps_per_beat=STEPS_PER_BEAT,
        bar_steps=[nearest_step(grid, bar.start) for bar in song.bars],
        chords=chords,
        warnings=warnings,
        timings=timings,
        elapsed=round(time.perf_counter() - began, 3),
    )
