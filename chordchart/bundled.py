"""Programs and folders that differ between the desktop app and a development checkout.

In the packaged Windows app (PyInstaller, `sys.frozen`), ffmpeg.exe and deno.exe ship
inside the app in `bin/`, and nothing may depend on what's installed on the PC. In
development they come from PATH (ffmpeg) and the `deno` Python package.

`UPDATE_HINT` is what a failed download suggests. The desktop app replaces the
developer command with its own "Update YouTube support" button.

`https_context()` is for the app's own urllib requests (Spotify, the yt-dlp updater).
A freshly installed Windows holds only a few root certificates and adds others when a
browser first needs them; Python can't trigger that, so HTTPS to some sites fails with
"certificate verify failed" (found by the Windows Sandbox test). certifi's bundle,
shipped with the app (yt-dlp and huggingface_hub already use it), is trusted as well.
"""

from __future__ import annotations

import os
import shutil
import ssl
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
    registers it with SetDllDirectory. torch first loads its DLLs with
    LoadLibraryExW(LOAD_LIBRARY_SEARCH_DEFAULT_DIRS), which ignores that folder, and only
    then retries with the plain search. os.add_dll_directory puts the folder in the first
    search too, so the app's own runtime is found either way, never a different one from
    the PC. (The Sandbox failure "WinError 126" on torch/lib/shm.dll was a runtime DLL
    missing from the bundle altogether, vcruntime140_threads.dll; chordchart.spec adds it.)
    Call it at startup, before anything imports torch.
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


def https_context() -> ssl.SSLContext:
    """TLS verification against Windows' certificate store plus certifi's bundle."""
    context = ssl.create_default_context()  # the Windows store
    try:
        import certifi
    except ImportError:
        return context
    context.load_verify_locations(cafile=certifi.where())
    return context
