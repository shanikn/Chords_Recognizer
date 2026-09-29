"""What the user typed -> a local audio file plus a title.

A link (http/https) is downloaded, or served from the cache, by download.py. A Spotify
track link is first matched to the same recording on YouTube (spotify.py; Spotify's own
audio is never used). Anything else is a path. Everything after this point handles all
of them identically.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from chordchart import spotify
from chordchart.download import default_cache_dir, download
from chordchart.errors import AudioDecodeError
from chordchart.fetch import DEFAULT_MAX_DURATION

# "youtube.com/..." or "youtu.be/...": a domain followed by a slash.
_LOOKS_LIKE_DOMAIN = re.compile(r"^([a-z0-9-]+\.)+[a-z]{2,}/", re.IGNORECASE)
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


@dataclass(frozen=True)
class ResolvedSource:
    path: Path  # local audio/video file
    title: str
    source: str  # what the user typed (for Spotify: the matched YouTube link)
    match: dict | None = None  # Spotify link -> YouTube video, see spotify.Match.to_dict


def is_link(arg: str) -> bool:
    parsed = urlparse(arg)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def normalize_link(url: str) -> str:
    """Reduce a YouTube link that names a video to just that video.

    `watch?v=ID&list=...&start_radio=1&index=3`, `youtu.be/ID?si=...` and
    `shorts/ID` all become `https://www.youtube.com/watch?v=ID`. So playlist/radio
    parameters are ignored, and the same video always hits the same cache entry.
    Anything else (other sites, playlist-only links) is returned unchanged.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    video_id = None
    if host in _YOUTUBE_HOSTS:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith(("/shorts/", "/live/", "/embed/")):
            video_id = parsed.path.split("/")[2]
    elif host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
    if video_id and _VIDEO_ID.match(video_id):
        return f"https://www.youtube.com/watch?v={video_id}"
    return url


def resolve_source(
    arg: str,
    *,
    max_duration: float = DEFAULT_MAX_DURATION,
    refresh: bool = False,
    status: Callable[[str], None] | None = None,
) -> ResolvedSource:
    if spotify.is_spotify(arg):
        folder = default_cache_dir() / "downloads"
        match = spotify.match_track(arg, folder, refresh=refresh, status=status)
        got = download(match.video.url, cache_dir=folder, max_duration=max_duration,
                       refresh=refresh, status=status)  # fmt: skip
        title = (
            f"{match.track.artist} - {match.track.title}"
            if match.track.artist
            else match.track.title
        )
        return ResolvedSource(got.path, title, match.video.url, match.to_dict())

    if is_link(arg):
        link = normalize_link(arg)
        if link != arg and status:
            status(f"using the video only: {link}")
        got = download(link, max_duration=max_duration, refresh=refresh, status=status)
        return ResolvedSource(got.path, got.title, link)

    path = Path(arg)
    if not path.is_file():
        hint = ""
        if _LOOKS_LIKE_DOMAIN.match(arg):
            hint = f" (if this is a link, add https:// : https://{arg})"
        raise AudioDecodeError(f"file not found: {path}{hint}")
    return ResolvedSource(path, path.stem, arg)
