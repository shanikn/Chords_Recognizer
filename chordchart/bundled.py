"""Programs and folders that differ between the desktop app and a development checkout.

In the packaged Windows app (PyInstaller, `sys.frozen`), ffmpeg.exe and deno.exe ship
inside the app in `bin/`, and nothing may depend on what's installed on the PC. In
development they come from PATH (ffmpeg) and the `deno` Python package.

`UPDATE_HINT` is what a failed download suggests. The desktop app replaces the
developer command with its own "Update YouTube support" button.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

DEV_UPDATE_HINT = "try: uv lock --upgrade-package yt-dlp && uv sync"
UPDATE_HINT = DEV_UPDATE_HINT


def app_data_dir() -> Path:
    """Per-user app folder: %LOCALAPPDATA%\\ChordChart (logs, yt-dlp updates, caches).
    Windows paths are case-insensitive, so it's the same folder as download.py's
    `%LOCALAPPDATA%\\chordchart\\cache`."""
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "ChordChart" if base else Path.home() / ".chordchart"


def bundle_dir() -> Path | None:
    """The unpacked app folder when frozen (PyInstaller's `_internal`), else None."""
    return Path(sys._MEIPASS) if FROZEN else None  # type: ignore[attr-defined]


def register_dll_folder() -> None:
    """Let native libraries find the Microsoft C/C++ runtime the app bundles.

    PyInstaller puts vcruntime140.dll, msvcp140.dll & co. in the bundle folder and
    registers it with SetDllDirectory. torch loads its DLLs with
    LoadLibraryExW(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS), which ignores that folder: on a PC
    without the Visual C++ Redistributable, torch/lib/shm.dll then fails with
    "WinError 126" (found by the Windows Sandbox test). os.add_dll_directory adds the
    folder to that search. Call it at startup, before anything imports torch.
    """
    folder = bundle_dir()
    if folder is not None and hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(folder))


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
