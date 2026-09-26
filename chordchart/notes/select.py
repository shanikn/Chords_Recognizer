"""Which stem is the main instrument: the loudest one, unless the user says otherwise.

Loudness is the RMS of the whole stem, in dBFS. It measures total energy, so an
instrument that plays through the song beats one that has a loud solo. In practice
Demucs leaves near-silence (below -60 dBFS) in the stems of instruments that aren't
there, so the gap to the real one is large.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from chordchart.notes.model import INSTRUMENTS

SILENT_DB = -120.0  # floor for an all-zero stem (log of 0), and still valid JSON


def stem_levels(stems: Mapping[str, np.ndarray]) -> dict[str, float]:
    """Each stem's RMS level in dBFS (full scale = 1.0), rounded to 0.1 dB."""
    levels = {}
    for name, audio in stems.items():
        rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64)))) if audio.size else 0.0
        levels[name] = round(max(20 * float(np.log10(rms)), SILENT_DB), 1) if rms else SILENT_DB
    return levels


def choose(levels: Mapping[str, float], instrument: str | None = None) -> tuple[str, bool]:
    """(stem to transcribe, chosen automatically?). `instrument` overrides the choice."""
    if instrument is not None:
        if instrument not in INSTRUMENTS:
            raise ValueError(f"instrument must be one of {', '.join(INSTRUMENTS)}")
        return instrument, False
    return max(levels, key=levels.__getitem__), True
