"""The interface every chord recognizer implements.

Keeping recognizers behind this protocol lets milestone 7 add alternatives (e.g.
Chordino running in WSL) and compare them in the eval harness without touching
the rest of the pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from chordchart.model import Segment


class ChordRecognizer(Protocol):
    name: str

    def recognize(self, wav_path: Path) -> list[Segment]:
        """Return contiguous chord segments (Harte labels) covering the whole file."""
        ...
