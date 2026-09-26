"""Cache of raw model outputs, so re-analysing the same audio skips the models.

Key: SHA-256 of the decoded WAV the models see (so it covers the song, the section and
its padding, and the ffmpeg decode), plus the recognizer, beats_per_bar and
CACHE_VERSION. Stored: only what the models produced (beats, raw chord segments, key).
Post-processing and the chart are recomputed every time, since they're cheap, so
changing them never serves a stale chart, and M5's option sweeps can reuse the cache.

Bump CACHE_VERSION whenever model-side code changes what the models return.
Files live in <cache root>/analysis/<key>.json. A corrupt or unreadable file is a
miss, never an error.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from chordchart.beats import Beats
from chordchart.model import Key, Segment

CACHE_VERSION = 1


@dataclass(frozen=True)
class ModelOutputs:
    beats: Beats
    segments: list[Segment]
    key: Key


def cache_key(wav: Path, recognizer: str, beats_per_bar: Sequence[int]) -> str:
    digest = hashlib.sha256()
    with open(wav, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    extra = f"|v{CACHE_VERSION}|{recognizer}|{','.join(map(str, beats_per_bar))}"
    digest.update(extra.encode())
    return digest.hexdigest()


# Only files named like our entries are ever counted or deleted.
_ENTRY = re.compile(r"^[0-9a-f]{64}\.(json|tmp)$")


def summary(folder: Path) -> tuple[int, int]:
    """(number of cached analyses, bytes used)."""
    files = _entries(folder)
    return sum(p.suffix == ".json" for p in files), sum(p.stat().st_size for p in files)


def clear(folder: Path) -> tuple[int, int]:
    """Delete every cached analysis; return what was removed, like summary()."""
    removed = summary(folder)
    for path in _entries(folder):
        path.unlink()
    return removed


def _entries(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in folder.iterdir() if p.is_file() and _ENTRY.match(p.name)]


def load(folder: Path, key: str) -> ModelOutputs | None:
    try:
        data = json.loads((folder / f"{key}.json").read_text(encoding="utf-8"))
        return ModelOutputs(
            beats=Beats(**data["beats"]),
            segments=[Segment(**s) for s in data["segments"]],
            key=Key(**data["key"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(folder: Path, key: str, outputs: ModelOutputs) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "beats": asdict(outputs.beats),
        "segments": [asdict(s) for s in outputs.segments],
        "key": asdict(outputs.key),
    }
    tmp = folder / f"{key}.tmp"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(folder / f"{key}.json")  # atomic: never a half-written entry
