"""What the user typed -> a local audio file plus a title.

A link (http/https) is downloaded, or served from the cache, by download.py. Anything
else is a path. Everything after this point handles both identically.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from chordchart.download import download
from chordchart.errors import AudioDecodeError
from chordchart.fetch import DEFAULT_MAX_DURATION

# "youtube.com/..." or "youtu.be/...": a domain followed by a slash.
_LOOKS_LIKE_DOMAIN = re.compile(r"^([a-z0-9-]+\.)+[a-z]{2,}/", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedSource:
    path: Path  # local audio/video file
    title: str
    source: str  # what the user typed


def is_link(arg: str) -> bool:
    parsed = urlparse(arg)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def resolve_source(
    arg: str,
    *,
    max_duration: float = DEFAULT_MAX_DURATION,
    refresh: bool = False,
    status: Callable[[str], None] | None = None,
) -> ResolvedSource:
    if is_link(arg):
        got = download(arg, max_duration=max_duration, refresh=refresh, status=status)
        return ResolvedSource(got.path, got.title, arg)

    path = Path(arg)
    if not path.is_file():
        hint = ""
        if _LOOKS_LIKE_DOMAIN.match(arg):
            hint = f" (if this is a link, add https:// : https://{arg})"
        raise AudioDecodeError(f"file not found: {path}{hint}")
    return ResolvedSource(path, path.stem, arg)
