"""Updating yt-dlp inside the packaged app, only when the user presses the button.

YouTube changes often, and yt-dlp follows with new releases. The app ships a copy of
yt-dlp, but it can't pip-install into itself. Instead:

- An update downloads the newest yt-dlp wheel from PyPI, plus the exact yt-dlp-ejs
  version it pins, and checks each file's SHA-256 against the one PyPI publishes.
- The wheels are unpacked into a staging folder, and a *separate process* imports
  them. Only if that works (and reports the expected version) does the staging folder
  become `<app data>/ytdlp/current`. A failed update leaves the working copy alone.
- At startup, `activate()` puts `current` first on sys.path, so the newer yt-dlp wins
  over the bundled one (verified with PyInstaller 6.22). If `current` doesn't import,
  it's set aside and the bundled copy is used, so a bad update can't stop the app.

Nothing here runs by itself; the page's "Update YouTube support" button calls update().
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from chordchart import bundled

log = logging.getLogger(__name__)

PYPI_JSON = "https://pypi.org/pypi/{name}/json"
PYPI_VERSION_JSON = "https://pypi.org/pypi/{name}/{version}/json"
CHECK_FLAG = "--check-ytdlp"  # the frozen app's entry point handles this (see app.py)


class UpdateError(Exception):
    """Shown to the user as one line; the working yt-dlp is untouched."""


@dataclass(frozen=True)
class UpdateResult:
    status: str  # "updated" | "up-to-date"
    version: str  # the version that will be used after a restart
    restart_needed: bool


def updates_dir() -> Path:
    return bundled.app_data_dir() / "ytdlp"


def activate(folder: Path | None = None, module: str = "yt_dlp") -> str | None:
    """Use an installed update if it imports. Call before anything imports yt-dlp.

    Returns the update's version, or None when the bundled copy is used.
    """
    current = (folder or updates_dir()) / "current"
    if not current.is_dir():
        return None
    sys.path.insert(0, str(current))
    try:
        version = __import__(f"{module}.version", fromlist=["__version__"]).__version__
        log.info("using updated %s %s from %s", module, version, current)
        return version
    except Exception:
        log.exception("the updated %s doesn't import; using the bundled copy", module)
        sys.path.remove(str(current))
        for name in [m for m in sys.modules if m == module or m.startswith(f"{module}.")]:
            del sys.modules[name]
        current.rename(current.with_name(f"broken-{int(time.time())}"))
        return None


def update(
    current_version: str,
    *,
    folder: Path | None = None,
    get_json: Callable[[str], dict] | None = None,
    get_bytes: Callable[[str], bytes] | None = None,
    check_import: Callable[[Path], str] | None = None,
) -> UpdateResult:
    """Install the newest yt-dlp if it's newer than `current_version`.

    The keyword arguments replace the network and the import check in tests.
    """
    get_json = get_json or _get_json
    get_bytes = get_bytes or _get_bytes
    check_import = check_import or _check_import
    folder = folder or updates_dir()

    try:
        latest = get_json(PYPI_JSON.format(name="yt-dlp"))["info"]["version"]
    except Exception as exc:
        raise UpdateError(f"couldn't reach PyPI to check for updates ({exc})") from None
    if _version_key(latest) <= _version_key(current_version):
        return UpdateResult("up-to-date", current_version, restart_needed=False)

    folder.mkdir(parents=True, exist_ok=True)
    staging = folder / f"staging-{uuid.uuid4().hex[:8]}"
    try:
        meta = get_json(PYPI_VERSION_JSON.format(name="yt-dlp", version=latest))
        _install_wheel(meta, staging, get_bytes)
        ejs = _pinned_ejs_version(meta)
        if ejs:
            ejs_meta = get_json(PYPI_VERSION_JSON.format(name="yt-dlp-ejs", version=ejs))
            _install_wheel(ejs_meta, staging, get_bytes)
        imported = check_import(staging)
        if _version_key(imported) != _version_key(latest):
            raise UpdateError(f"the new yt-dlp reported version {imported}, expected {latest}")
    except UpdateError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise UpdateError(f"the update failed ({exc}); the current version still works") from None

    current = folder / "current"
    if current.exists():
        old = folder / f"old-{int(time.time())}"
        current.rename(old)
        shutil.rmtree(old, ignore_errors=True)
    staging.rename(current)
    log.info("yt-dlp updated to %s", latest)
    return UpdateResult("updated", latest, restart_needed=True)


def _install_wheel(meta: dict, target: Path, get_bytes: Callable[[str], bytes]) -> None:
    wheels = [u for u in meta["urls"] if u["filename"].endswith("-py3-none-any.whl")]
    if not wheels:
        info = meta["info"]
        raise UpdateError(f"no pure-Python wheel for {info['name']} {info['version']}")
    wheel = wheels[0]
    data = get_bytes(wheel["url"])
    if hashlib.sha256(data).hexdigest() != wheel["digests"]["sha256"]:
        raise UpdateError(f"{wheel['filename']} failed its SHA-256 check; nothing was installed")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        archive.extractall(target)  # zipfile drops absolute paths and ".." parts


def _pinned_ejs_version(meta: dict) -> str | None:
    for requirement in meta["info"].get("requires_dist") or []:
        found = re.match(r"yt-dlp-ejs\s*==\s*([\w.]+)\s*;\s*extra\s*==\s*.default.", requirement)
        if found:
            return found.group(1)
    return None


def _version_key(version: str) -> tuple[int, ...]:
    """ "2026.8.19" and "2026.08.19" compare equal; ".post1" style tails are ignored."""
    return tuple(int(part) for part in re.findall(r"\d+", version)[:3])


def _get_json(url: str) -> dict:
    return json.loads(_get_bytes(url))


def _get_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ChordChart-updater"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _check_import(folder: Path) -> str:
    """Import yt-dlp from `folder` in a separate process; return its version."""
    result_file = folder / "check-result.json"
    if bundled.FROZEN:
        cmd = [sys.executable, CHECK_FLAG, str(folder), str(result_file)]
    else:
        module = "chordchart.desktop.ytdlp_update"
        cmd = [sys.executable, "-m", module, str(folder), str(result_file)]
    subprocess.run(cmd, timeout=120, capture_output=True, creationflags=_NO_WINDOW)
    try:
        result = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UpdateError("the new yt-dlp couldn't be tested; nothing was changed") from None
    finally:
        result_file.unlink(missing_ok=True)
    if not result.get("ok"):
        error = result.get("error")
        raise UpdateError(f"the new yt-dlp doesn't load ({error}); nothing was changed")
    return result["version"]


def run_import_check(folder: str, result_file: str) -> int:
    """Child-process side of _check_import (also the frozen app's --check-ytdlp)."""
    sys.path.insert(0, folder)
    try:
        import yt_dlp
        import yt_dlp.version
        import yt_dlp_ejs  # noqa: F401  (YouTube's JavaScript challenge solver)

        if not str(Path(yt_dlp.__file__).resolve()).startswith(str(Path(folder).resolve())):
            raise ImportError(f"imported the wrong yt-dlp: {yt_dlp.__file__}")
        yt_dlp.YoutubeDL({"quiet": True})  # exercises most of its imports
        result = {"ok": True, "version": yt_dlp.version.__version__}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(result_file).write_text(json.dumps(result), encoding="utf-8")
    return 0 if result["ok"] else 1


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flash on Windows

if __name__ == "__main__":
    sys.exit(run_import_check(sys.argv[1], sys.argv[2]))
