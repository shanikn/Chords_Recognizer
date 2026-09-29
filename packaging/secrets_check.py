"""Refuse to ship the builder's own Spotify key or user files in the app.

Run by build.py on the built app folder, before it is zipped or put in an installer:

- no file there may be one of the app's per-user files: settings.json (may hold a
  Spotify key) or spotify.json (the builder's Spotify matches). (Not index.json, the
  downloads index: librosa ships a file of that name.)
- if this PC has a Spotify key (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET in the
  environment, or in %LOCALAPPDATA%\\ChordChart\\settings.json), no file may contain it.

The app only ever reads a key at run time, from the user's own environment or data
folder, so a hit means something was copied in by mistake.

    uv run python packaging/secrets_check.py packaging/dist/ChordChart
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path

USER_FILES = {"settings.json", "spotify.json"}
KEY_NAMES = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET")
SETTING_NAMES = ("spotify_client_id", "spotify_client_secret")
MIN_SECRET = 8  # shorter values would match by chance
CHUNK = 16 * 2**20


def local_secrets(environ=os.environ, data_dir: Path | None = None) -> list[str]:
    """The Spotify key values present on this PC (environment and settings.json)."""
    values = [environ.get(name, "") for name in KEY_NAMES]
    if data_dir is None and (local := environ.get("LOCALAPPDATA")):
        data_dir = Path(local) / "ChordChart"
    if data_dir is not None:
        try:
            settings = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        if isinstance(settings, dict):
            values += [str(settings.get(name) or "") for name in SETTING_NAMES]
    return sorted({v for v in values if len(v) >= MIN_SECRET})


def problems(folder: Path, secrets: Iterable[str]) -> list[str]:
    """Why `folder` must not be shipped; empty if it's fine."""
    found = []
    needles = [s.encode() for s in secrets] + [s.encode("utf-16-le") for s in secrets]
    overlap = max((len(n) for n in needles), default=1) - 1
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        name = path.relative_to(folder)
        if path.name.lower() in USER_FILES:
            found.append(f"{name}: a per-user file must not be bundled")
        if not needles:
            continue
        with path.open("rb") as handle:
            tail = b""
            while block := handle.read(CHUNK):
                data = tail + block
                if any(n in data for n in needles):
                    found.append(f"{name}: contains this PC's Spotify key")
                    break
                tail = data[-overlap:] if overlap else b""
    return found


def check(folder: Path) -> None:
    """Exit with the problems if `folder` holds user files or this PC's Spotify key."""
    secrets = local_secrets()
    found = problems(folder, secrets)
    if found:
        raise SystemExit("refusing to ship this build:\n  " + "\n  ".join(found))
    what = f"{len(secrets)} local key value(s) searched" if secrets else "no local Spotify key set"
    print(f"secrets check: OK ({what})")


if __name__ == "__main__":
    check(Path(sys.argv[1]))
