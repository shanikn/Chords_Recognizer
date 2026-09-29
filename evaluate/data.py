"""The evaluation songs and their reference annotations.

The Isophonics Beatles archive is downloaded once into evaluate/data/ (gitignored) and
checked against its SHA-256; nothing from it is committed. Audio comes from each
song's link through chordchart's own download cache.
"""

from __future__ import annotations

import hashlib
import tarfile
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
ARCHIVE_URL = "https://isophonics.net/files/annotations/The%20Beatles%20Annotations.tar.gz"
ARCHIVE_SHA256 = "e642f7f999c1df791326358050ce6cbb99684137c1d9cdd3e89dae6e87c81fdc"
ARCHIVE = DATA / "The Beatles Annotations.tar.gz"
ANNOTATIONS = DATA / "isophonics"


@dataclass(frozen=True)
class EvalSong:
    slug: str
    title: str
    path: str  # inside the archive's chordlab/ and beat/ folders
    link: str

    @property
    def chord_file(self) -> Path:
        return ANNOTATIONS / "chordlab" / "The Beatles" / f"{self.path}.lab"

    @property
    def beat_file(self) -> Path:
        return ANNOTATIONS / "beat" / "The Beatles" / f"{self.path}.txt"


def load_songs(path: Path = HERE / "songs.toml") -> list[EvalSong]:
    with open(path, "rb") as f:
        return [EvalSong(**entry) for entry in tomllib.load(f)["song"]]


def ensure_annotations() -> Path:
    """Download (once) and unpack the Isophonics Beatles annotations; return their folder."""
    if (ANNOTATIONS / "chordlab").is_dir() and (ANNOTATIONS / "beat").is_dir():
        return ANNOTATIONS
    DATA.mkdir(parents=True, exist_ok=True)
    if not ARCHIVE.exists():
        print(f"downloading the Isophonics Beatles annotations: {ARCHIVE_URL}")
        partial = ARCHIVE.with_suffix(".part")
        urllib.request.urlretrieve(ARCHIVE_URL, partial)
        partial.replace(ARCHIVE)
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    if digest != ARCHIVE_SHA256:
        ARCHIVE.unlink()
        raise SystemExit(f"the annotation archive failed its SHA-256 check ({digest}); deleted it")
    with tarfile.open(ARCHIVE) as archive:
        archive.extractall(ANNOTATIONS, filter="data")
    return ANNOTATIONS


def read_chords(path: Path) -> tuple[np.ndarray, list[str]]:
    """(intervals (n, 2), Harte labels) from an Isophonics .lab file."""
    import mir_eval

    intervals, labels = mir_eval.io.load_labeled_intervals(str(path))
    return intervals, list(labels)


def read_beats(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """(beat times, positions in the bar; 1 = downbeat) from an Isophonics beat file."""
    rows = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    times = np.array([float(r[0]) for r in rows])
    positions = np.array([int(float(r[1])) for r in rows])
    return times, positions
