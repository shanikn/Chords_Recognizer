"""Programs and folders that differ between the desktop app and a development checkout.

In the packaged Windows app (PyInstaller, `sys.frozen`), ffmpeg.exe and deno.exe ship
inside the app in `bin/`, and nothing may depend on what's installed on the PC. In
development they come from PATH (ffmpeg) and the `deno` Python package.

`UPDATE_HINT` is what a failed download suggests. The desktop app replaces the
developer command with its own "Update YouTube support" button.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

DEV_UPDATE_HINT = "try: uv lock --upgrade-package yt-dlp && uv sync"
UPDATE_HINT = DEV_UPDATE_HINT


def bundle_dir() -> Path | None:
    """The unpacked app folder when frozen (PyInstaller's `_internal`), else None."""
    return Path(sys._MEIPASS) if FROZEN else None  # type: ignore[attr-defined]


def bundled_exe(name: str) -> str | None:
    """`bin/<name>.exe` inside the frozen app, if it's there."""
    folder = bundle_dir()
    if folder is None:
        return None
    path = folder / "bin" / f"{name}.exe"
    return str(path) if path.is_file() else None


def ffmpeg_path() -> str | None:
    return bundled_exe("ffmpeg") or shutil.which("ffmpeg")


def deno_path() -> str | None:
    """The JavaScript runtime yt-dlp needs for YouTube."""
    if FROZEN:
        return bundled_exe("deno")
    try:
        from deno import find_deno_bin  # the `deno` package from yt-dlp[deno]

        return find_deno_bin()
    except Exception:  # not installed, or its binary is missing: yt-dlp searches PATH
        return shutil.which("deno")
