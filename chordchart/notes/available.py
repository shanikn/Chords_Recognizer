"""Is the notes feature installed? The lite desktop app ("ChordChart") leaves it out.

Only looks the packages up (importlib.util.find_spec), never imports them: importing
torch takes seconds, and this runs at startup. In the frozen app, PyInstaller's
importer answers find_spec for what's bundled, so an excluded package is None here.
"""

from __future__ import annotations

import importlib.util
from functools import cache

# What notes need at run time, by import name.
PACKAGES = ("torch", "demucs", "basic_pitch", "onnxruntime")
MISSING = "notes are not included in this version of ChordChart (install ChordChart Notes)"


@cache
def notes_available() -> bool:
    try:
        return all(importlib.util.find_spec(name) is not None for name in PACKAGES)
    except (ImportError, ValueError):
        return False
