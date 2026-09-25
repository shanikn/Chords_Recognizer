"""Links -> cached local audio files, via yt-dlp.

The cache is a folder of `<extractor>-<id>.<ext>` audio files, each with a sidecar
`<extractor>-<id>.json` (title, duration, URL), plus `index.json` mapping every link
string we've seen to its file stem:

1. Same link again: an index hit. No network at all, so it works offline too.
2. Another link to a cached video (`youtu.be/x` vs `watch?v=x`): one metadata request
   to learn the id, then the existing file is reused. No download.
3. Otherwise: download once.

We key by what yt-dlp resolves, not by parsing the link ourselves: `watch?v=X&list=Y`
parses as the *playlist* Y offline, but yt-dlp with `noplaylist` resolves it to video X.

yt-dlp reports every failure as a DownloadError. `classify` turns those into our one-line
errors (spec §8), based on the wrapped exception rather than the message text, which
changes more often.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.networking.exceptions import TransportError
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

from chordchart.errors import (
    AudioRejectedError,
    ChordChartError,
    DownloadFailedError,
    InvalidLinkError,
    NetworkError,
    VideoUnavailableError,
)
from chordchart.fetch import DEFAULT_MAX_DURATION
from chordchart.timecode import format_time

log = logging.getLogger(__name__)

INDEX = "index.json"
_NOT_MEDIA = {".json", ".part", ".ytdl", ".tmp"}


@dataclass(frozen=True)
class Downloaded:
    path: Path
    title: str
    duration: float | None  # seconds, if the site reports it
    webpage_url: str
    cached: bool


def default_cache_dir() -> Path:
    """Cache root: $CHORDCHART_CACHE_DIR, else %LOCALAPPDATA%/chordchart/cache
    (~/.cache/chordchart off Windows). Downloads live in its `downloads` folder."""
    if env := os.environ.get("CHORDCHART_CACHE_DIR"):
        return Path(env)
    if local := os.environ.get("LOCALAPPDATA"):
        return Path(local) / "chordchart" / "cache"
    return Path.home() / ".cache" / "chordchart"


def download(
    url: str,
    *,
    cache_dir: Path | None = None,
    max_duration: float = DEFAULT_MAX_DURATION,
    refresh: bool = False,
    status: Callable[[str], None] | None = None,
    ydl_class: type = yt_dlp.YoutubeDL,
) -> Downloaded:
    """Return the audio for `url`, downloading it only if it isn't cached.

    `cache_dir` is the downloads folder itself (default: default_cache_dir()/downloads).
    `status` receives one-line progress messages meant for stderr.
    """
    folder = Path(cache_dir) if cache_dir else default_cache_dir() / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    say = status or (lambda message: None)
    index = _read_json(folder / INDEX)

    if not refresh and (stem := index.get(url)) and (hit := _cached(folder, stem)):
        say(f"using cached download: {hit.title}")
        return hit

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(folder / "%(extractor)s-%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
        "overwrites": refresh,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _Logger(),
        "socket_timeout": 20,
    }
    try:
        with ydl_class(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            _check(info, url, max_duration)
            stem = Path(ydl.prepare_filename(info)).stem
            if not refresh and (hit := _cached(folder, stem)):
                _record(folder, index, url, stem)
                say(f"using cached download: {hit.title}")
                return hit
            duration = info.get("duration")
            length = f" ({format_time(duration)})" if duration else ""
            say(f"downloading: {info.get('title', url)}{length}")
            info = ydl.extract_info(url, download=True)
    except DownloadError as err:
        raise classify(err, url) from err

    path = Path(info["requested_downloads"][0]["filepath"])
    meta = {
        "title": info.get("title") or path.stem,
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url") or url,
    }
    _write_json(folder / f"{path.stem}.json", meta)
    _record(folder, index, url, path.stem)
    return Downloaded(path=path, cached=False, **meta)


def classify(err: DownloadError, url: str) -> ChordChartError:
    """Turn a yt-dlp DownloadError into one of our one-line errors."""
    cause = err.exc_info[1] if err.exc_info else None
    if any(isinstance(c, TransportError) for c in _cause_chain(cause)):
        host = urlparse(url).hostname or url
        return NetworkError(
            f"network error: could not reach {host}; check your internet connection"
        )
    if isinstance(cause, UnsupportedError):
        return InvalidLinkError(f"not a single-video link: {url} (unsupported site)")
    if isinstance(cause, ExtractorError) and cause.expected:
        return VideoUnavailableError(f"video unavailable: {_reason(err)}")
    return DownloadFailedError(
        f"download failed: {_reason(err)}. YouTube changes often; "
        "try: uv lock --upgrade-package yt-dlp && uv sync"
    )


def _check(info: dict, url: str, max_duration: float) -> None:
    """Refuse what we can't analyse, before downloading anything."""
    if info.get("_type") in ("playlist", "multi_video") or "entries" in info:
        # Also catches a malformed `watch?v=` link, which yt-dlp resolves to a feed.
        raise InvalidLinkError(f"not a single-video link: {url} (playlist or feed page)")
    if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
        raise InvalidLinkError(f"not a single-video link: {url} (live stream)")
    duration = info.get("duration")
    if duration and duration > max_duration:
        raise AudioRejectedError(
            f"video is {format_time(duration)} long, over the {max_duration / 60:g} min limit; "
            "raise --max-duration to download it"
        )


def _cached(folder: Path, stem: str) -> Downloaded | None:
    """The finished download for `stem`, if both it and its sidecar exist."""
    meta = _read_json(folder / f"{stem}.json")
    if not meta:
        return None
    for path in folder.glob(f"{stem}.*"):
        # Exactly "<stem>.<ext>": skips the sidecar and yt-dlp's .part/.ytdl leftovers.
        if path.suffix not in _NOT_MEDIA and "." not in path.name[len(stem) + 1 :]:
            return Downloaded(path=path, cached=True, **meta)
    return None


def _record(folder: Path, index: dict, url: str, stem: str) -> None:
    index[url] = stem
    _write_json(folder / INDEX, index)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)  # atomic: a crash never leaves a half-written index


def _cause_chain(exc: BaseException | None):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = getattr(exc, "cause", None) or exc.__cause__ or exc.__context__


def _reason(err: DownloadError) -> str:
    """yt-dlp's message without its prefixes, bug-report boilerplate and line breaks."""
    text = str(err)
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)  # colour codes
    text = re.sub(r"^ERROR:\s*", "", text)
    text = re.sub(r"^\[[^\]]+\]\s+[^:\s]+:\s*", "", text)  # "[youtube] <id>: "
    text = re.split(r"\s*\(caused by |;\s*please report this issue", text)[0]
    return " ".join(text.split()).rstrip(".")


class _Logger:
    """Keeps yt-dlp's console output out of our stdout/stderr."""

    def debug(self, msg: str) -> None:
        log.debug(msg)

    def info(self, msg: str) -> None:
        log.debug(msg)

    def warning(self, msg: str) -> None:
        log.debug("yt-dlp warning: %s", msg)

    def error(self, msg: str) -> None:
        log.debug("yt-dlp error: %s", msg)  # re-raised as a DownloadError and classified
