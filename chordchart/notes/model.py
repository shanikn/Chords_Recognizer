"""The notes feature's data: one `Note` per played note, one `Transcription` per song."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

# The stems Demucs htdemucs_6s produces that can carry the main instrument. Its other
# two, vocals and drums, are discarded: basic-pitch can't turn either into useful notes.
INSTRUMENTS = ("bass", "guitar", "piano", "other")


@dataclass(frozen=True)
class Note:
    start: float  # seconds on the source's timeline, like every time in a Song
    end: float
    pitch: int  # MIDI note number, 60 = middle C
    velocity: int  # 1-127
    # Position on the chart's 16th-note grid, set by quantize(). -1 = not quantized.
    step: int = -1
    steps: int = 0  # length in 16ths


@dataclass
class Transcription:
    title: str
    source: str
    instrument: str  # one of INSTRUMENTS
    automatic: bool  # True if chosen as the loudest stem, False if the user picked it
    levels: dict[str, float]  # each candidate stem's loudness in dBFS (RMS)
    notes: list[Note]
    bpm: float
    meter: int  # beats per bar
    # The chart's range (seconds); the grid's step 0 is at `section_start`.
    section_start: float
    section_end: float
    steps_per_beat: int = 4
    # Downbeats as grid steps, and the chord symbol starting at each chord change, so
    # the piano roll can draw bar lines and chord names in step units.
    bar_steps: list[int] = field(default_factory=list)
    chords: list[tuple[int, str]] = field(default_factory=list)  # (step, symbol)
    # The song's key from the chord analysis ("G#", "minor"), for the key signature.
    key_tonic: str = ""
    key_mode: str = ""
    steps: int = 0  # length of the grid in steps: the chart's end
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    elapsed: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
