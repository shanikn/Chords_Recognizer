"""Shared bits of the experiment scripts: where outputs go, which songs they use."""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DOWNLOADS = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "chordchart" / "cache" / "downloads"


def out_dir(name: str) -> Path:
    """experiments/out/<name>, or <argv[1]>/<name>; never committed (.gitignore)."""
    base = (
        Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else HERE / "out"
    )
    folder = base / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def cached_songs() -> list[Path]:
    """Every song chordchart has downloaded (its download cache), sorted."""
    songs = sorted(DOWNLOADS.glob("*.webm")) + sorted(DOWNLOADS.glob("*.m4a"))
    if not songs:
        raise SystemExit(
            f"no cached songs in {DOWNLOADS}: analyse a few links with chordchart first"
        )
    return songs


# The four songs the timing experiments used (2026-09-27), all in the download cache.
TIMING_SONGS = {
    "Adele - Someone Like You": "youtube-hLQl3WQQoQ0.webm",
    "Cheek To Cheek": "youtube-20iOlPwz0J0.webm",
    "Fur Elise": "youtube-q9bU12gXUyM.webm",
    "Riptide": "youtube-uJ_1HMAGb4k.webm",
}

# Songs the Beat This! references are made for (export.py --refs, verify.py).
REFERENCE_SONGS = ["youtube-hLQl3WQQoQ0", "youtube-q9bU12gXUyM", "youtube-20iOlPwz0J0"]
