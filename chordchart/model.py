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
    duration: float  # seconds analysed (the section's length when one is selected)
    key: Key
    bpm: float
    meter: int  # beats per bar
    bars: list[Bar]
    warnings: list[str] = field(default_factory=list)
    # The analysed section of the source, in seconds, widened to whole bars. All times
    # in the Song (bars, chords, debug segments) are absolute, measured from the start
    # of the source.
    section_start: float = 0.0
    section_end: float | None = None  # None = to the end of the source
    # What the user asked for (--start/--end), before widening. None = no section.
    requested_start: float | None = None
    requested_end: float | None = None
    # Seconds per pipeline stage: download, models, decode, beats, chords, key, chart.
    # Beats, chords and key can run at the same time, so they don't add up to...
    timings: dict[str, float] = field(default_factory=dict)
    # ...the wall-clock seconds for the whole analysis.
    elapsed: float = 0.0
    # For a Spotify link: the track and the YouTube video analysed instead
    # (spotify.Match.to_dict). None for other sources.
    match: dict | None = None
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
