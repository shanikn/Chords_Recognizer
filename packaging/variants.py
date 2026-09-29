"""The two desktop builds, and which Python packages only the notes feature needs.

    lite  "ChordChart"        chords only
    full  "ChordChart Notes"  chords and notes (torch, Demucs, basic-pitch, ...)

Notes-only packages are computed, not listed: every distribution reachable from
chordchart's runtime dependencies, minus those still reachable once NOTES_ROOTS are
cut off. So numpy and scipy (madmom needs them) stay in lite, and torch, librosa, numba
and friends go. The spec (PyInstaller excludes) and collect_licenses.py (notices) both
use this, so the bundle and its license list can't disagree.

Used by build.py, collect_licenses.py and chordchart.spec, which all run with
packaging/ importable (build.py and the spec put it on sys.path).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from importlib import metadata

from packaging.requirements import Requirement

# onnxruntime isn't one: the default beat tracker (Beat This!) runs on it in both builds.
NOTES_ROOTS = ("demucs", "basic-pitch")
# Bundled in both apps besides chordchart itself: the desktop dependency group's window.
APP_ROOTS = ("pywebview",)
ENV = "CHORDCHART_VARIANT"  # how build.py tells the spec which variant to build


@dataclass(frozen=True)
class Variant:
    key: str  # lite | full
    app_name: str  # window/installer name, and the dist folder
    file_stem: str  # zip and installer names: <stem>-<version>-win64.zip
    app_id: str  # Inno Setup AppId: different, so both can be installed side by side
    notes: bool
    # In %LOCALAPPDATA%\ChordChart\logs; must match chordchart.desktop.app.log_name, so
    # the two apps running at once never rotate (rename) each other's open log.
    log_file: str


VARIANTS = {
    "lite": Variant(
        "lite",
        "ChordChart",
        "ChordChart",
        "{{8E4B6C2A-6F2D-4C1E-9B7A-3C5D2E1F0A9B}",
        False,
        "chordchart.log",
    ),
    "full": Variant(
        "full",
        "ChordChart Notes",
        "ChordChartNotes",
        "{{3F7D1B9E-2C4A-4E8B-A6D5-9B1E7C3F2A48}",
        True,
        "chordchart-notes.log",
    ),
}
DEFAULT = "lite"


def current() -> Variant:
    """The variant being built (from the environment, set by build.py)."""
    return VARIANTS[os.environ.get(ENV, DEFAULT)]


def reachable(roots, excluded) -> dict[str, metadata.Distribution]:
    """Distributions reachable from `roots` through runtime requirements, by normalized
    name, never entering an `excluded` one. The extras the app uses are followed.

    A distribution is expanded once per set of extras it's asked for: `lib` and later
    `lib[fast]` are two expansions, the second following what `fast` enables (a
    requirement marked `extra == "fast"`). The (name, extras) pairs already expanded are
    remembered, so cycles through extras end.
    """
    seen: dict[str, metadata.Distribution] = {}
    expanded: set[tuple[str, frozenset[str]]] = set()
    todo = [(name, frozenset()) for name in roots]
    while todo:
        name, extras = todo.pop()
        key = normal(name)
        if key in excluded or (key, extras) in expanded:
            continue
        expanded.add((key, extras))
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        seen[key] = dist
        wanted = extras | ({"default", "deno"} if key == "yt-dlp" else frozenset())
        envs = [{"extra": e} for e in wanted] or [{"extra": ""}]
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker and not any(req.marker.evaluate(env) for env in envs):
                continue
            todo.append((req.name, frozenset(req.extras)))
    return seen


def notes_only(excluded=frozenset()) -> set[str]:
    """Normalized names of the distributions only the notes feature pulls in. Both apps
    also bundle APP_ROOTS (the desktop window), so what those need is never notes-only:
    pythonnet needs cffi, which demucs' soundfile needs too."""
    roots = ["chordchart", *APP_ROOTS]
    everything = reachable(roots, set(excluded))
    without_notes = reachable(roots, set(excluded) | {normal(n) for n in NOTES_ROOTS})
    return set(everything) - set(without_notes)


def notes_only_modules(excluded=frozenset()) -> list[str]:
    """Top-level import names of the notes-only distributions, for PyInstaller."""
    dists = notes_only(excluded)
    modules = {
        module
        for module, owners in metadata.packages_distributions().items()
        if any(normal(owner) in dists for owner in owners)
    }
    # Only real top-level packages/modules: skip "__pycache__", "_distutils_hack" etc.
    return sorted(m for m in modules if m.isidentifier() and not m.startswith("__"))


def normal(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


if __name__ == "__main__":  # python packaging/variants.py: show what lite leaves out
    names = sorted(notes_only())
    print(f"{len(names)} notes-only distributions: {', '.join(names)}")
    print(f"modules excluded from lite: {', '.join(notes_only_modules())}", file=sys.stderr)
