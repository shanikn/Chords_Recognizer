"""Spotify track links -> the same song on YouTube.

Spotify's audio can't be used: it is DRM-protected, the Developer Terms forbid ripping
it or feeding Spotify content into ML models, and 30 s previews were withdrawn from new
apps (Nov 2024). So nothing here downloads or records audio from Spotify. What we take
from Spotify is the track's *metadata* (title, artists, duration), then:

1. search YouTube for "<artist> - <title>" (yt-dlp `ytsearch5`, metadata only);
2. keep results within ±3 s of the Spotify duration, preferring the artist's
   auto-generated "<Artist> - Topic" channel (the studio recording), then the closest
   duration; if none is within ±3 s, report "no confident match";
3. the matched video goes through the normal YouTube download and analysis.

Metadata comes from the Spotify Web API (client credentials) when a key is configured
(SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET, or settings.json in the app's data folder),
otherwise from the public track page's <meta> tags (title, artist, duration).

The track -> video choice is cached (`spotify.json` in the downloads folder), and the
user can override it with a YouTube link of their own, which is kept until they choose
another. Spotify's terms allow caching metadata temporarily, not indefinitely: after
MATCH_MAX_AGE a searched match is searched again, and a user's choice keeps its video but
fetches the track's title, artists and length again.
Only single tracks are supported: albums, playlists, artists, podcasts and short links
get a clear error.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from chordchart import bundled
from chordchart.errors import InvalidLinkError, NetworkError, SpotifyMatchError

log = logging.getLogger(__name__)

TOLERANCE = 3.0  # seconds between Spotify's and the video's duration
SEARCH_RESULTS = 5
MATCHES = "spotify.json"
SETTINGS = "settings.json"
# Spotify asks apps not to keep its content indefinitely: refresh it after this long.
MATCH_MAX_AGE = 30 * 24 * 3600

_HOSTS = {"open.spotify.com", "play.spotify.com"}
_SHORT_HOSTS = {"spotify.link", "spotify.app.link"}
_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_KINDS = {
    "album": "an album",
    "playlist": "a playlist",
    "artist": "an artist",
    "show": "a podcast",
    "episode": "a podcast episode",
    "user": "a profile",
    "collection": "your library",
}
SUPPORTED_HINT = "only single-track links are supported, like https://open.spotify.com/track/..."


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artists: list[str]
    duration: float | None  # seconds

    @property
    def url(self) -> str:
        return f"https://open.spotify.com/track/{self.id}"

    @property
    def artist(self) -> str:
        return ", ".join(self.artists)


@dataclass(frozen=True)
class Video:
    id: str
    title: str
    channel: str
    duration: float | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass(frozen=True)
class Match:
    track: Track
    video: Video
    chosen_by: str  # "search" or "user"

    def to_dict(self) -> dict:
        return {
            "spotify_url": self.track.url,
            "track": asdict(self.track),
            "video": {**asdict(self.video), "url": self.video.url},
            "chosen_by": self.chosen_by,
        }


def is_spotify(arg: str) -> bool:
    if arg.startswith("spotify:"):
        return True
    host = (urllib.parse.urlparse(arg).hostname or "").lower()
    return host in _HOSTS or host in _SHORT_HOSTS


def track_id(arg: str) -> str:
    """The track id of a Spotify track link or URI; InvalidLinkError for anything else
    (album, playlist, artist, podcast, short link), with what to do instead."""
    if arg.startswith("spotify:"):
        parts = arg.split(":")
        kind, ident = (parts[1], parts[2]) if len(parts) >= 3 else ("", "")
    else:
        parsed = urllib.parse.urlparse(arg)
        host = (parsed.hostname or "").lower()
        if host in _SHORT_HOSTS:
            raise InvalidLinkError(
                "Spotify short links aren't supported: open it in a browser and copy the "
                "open.spotify.com/track/... address instead"
            )
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0].startswith("intl-"):  # /intl-de/track/…
            parts = parts[1:]
        if parts[:1] == ["embed"]:
            parts = parts[1:]
        kind, ident = (parts[0], parts[1]) if len(parts) >= 2 else ("", "")
    if kind == "track" and _ID.match(ident):
        return ident
    what = _KINDS.get(kind)
    if what:
        raise InvalidLinkError(f"this Spotify link is {what}; {SUPPORTED_HINT}")
    raise InvalidLinkError(f"not a Spotify track link: {arg}; {SUPPORTED_HINT}")


# --- metadata ---------------------------------------------------------------------


def _http(request: urllib.request.Request | str, timeout: float = 15) -> bytes:
    try:
        context = bundled.https_context()  # a fresh Windows lacks some root certificates
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError:
        raise  # the server answered: callers decide what a 404 or 401 means
    except (urllib.error.URLError, TimeoutError, OSError) as err:
        log.warning("Spotify request failed: %r", err)  # the reason, in the app's log
        raise NetworkError(
            "network error: could not reach Spotify; check your internet connection"
        ) from err


def credentials(data_dir: Path | None = None) -> tuple[str, str] | None:
    """(client id, secret) from the environment, else settings.json; None if unset."""
    cid, secret = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if cid and secret:
        return cid, secret
    if data_dir is not None:
        try:
            settings = json.loads((data_dir / SETTINGS).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            settings = {}
        cid, secret = settings.get("spotify_client_id"), settings.get("spotify_client_secret")
        if cid and secret:
            return str(cid), str(secret)
    return None


def track_from_api(ident: str, creds: tuple[str, str], http: Callable = _http) -> Track:
    basic = base64.b64encode(f"{creds[0]}:{creds[1]}".encode()).decode()
    token_request = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=b"grant_type=client_credentials",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    token = json.loads(http(token_request))["access_token"]
    track_request = urllib.request.Request(
        f"https://api.spotify.com/v1/tracks/{ident}", headers={"Authorization": f"Bearer {token}"}
    )
    data = json.loads(http(track_request))
    return Track(
        id=ident,
        title=data["name"],
        artists=[a["name"] for a in data.get("artists", [])],
        duration=data["duration_ms"] / 1000 if data.get("duration_ms") else None,
    )


_META = re.compile(r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', re.IGNORECASE)


def track_from_page(ident: str, http: Callable = _http) -> Track:
    """Title, artist and duration from the public track page's <meta> tags."""
    request = urllib.request.Request(
        f"https://open.spotify.com/track/{ident}", headers={"User-Agent": "Mozilla/5.0"}
    )
    page = http(request).decode("utf-8", errors="replace")
    meta: dict[str, list[str]] = {}
    for name, content in _META.findall(page):
        meta.setdefault(name, []).append(html.unescape(content))
    title = (meta.get("og:title") or [""])[0]
    artists = meta.get("music:musician_description") or []
    if not artists and (description := (meta.get("og:description") or [""])[0]):
        artists = [description.split(" · ")[0]]  # "Artist · Album · Song · 2004"
    duration = (meta.get("music:duration") or [None])[0]
    if not title:
        raise SpotifyMatchError(
            "couldn't read this track's details from Spotify's page; paste a YouTube link of "
            "the song instead"
        )
    return Track(
        id=ident, title=title, artists=artists, duration=float(duration) if duration else None
    )


def fetch_track(ident: str, data_dir: Path | None = None, http: Callable = _http) -> Track:
    if creds := credentials(data_dir):
        try:
            return track_from_api(ident, creds, http)
        except urllib.error.HTTPError as err:
            if err.code == 404:
                raise SpotifyMatchError("Spotify doesn't know this track (check the link)") from err
            # A wrong or revoked key shouldn't stop the analysis: the public page works too.
    try:
        return track_from_page(ident, http)
    except urllib.error.HTTPError as err:
        if err.code == 404:
            raise SpotifyMatchError("Spotify doesn't know this track (check the link)") from err
        raise NetworkError(f"network error: Spotify answered {err.code}") from err


# --- YouTube search -----------------------------------------------------------------


def search_youtube(query: str, ydl_class: type | None = None) -> list[Video]:
    """The top results for `query`, metadata only (nothing downloaded)."""
    from yt_dlp.utils import DownloadError

    from chordchart.download import _Logger, classify

    if ydl_class is None:
        import yt_dlp

        ydl_class = yt_dlp.YoutubeDL
    opts = {"extract_flat": "in_playlist", "quiet": True, "no_warnings": True,
            "logger": _Logger(), "socket_timeout": 20}  # fmt: skip
    try:
        with ydl_class(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{SEARCH_RESULTS}:{query}", download=False)
    except DownloadError as err:
        raise classify(err, "https://www.youtube.com") from err
    videos = []
    for entry in info.get("entries") or []:
        if not entry or not entry.get("id"):
            continue
        videos.append(
            Video(
                id=entry["id"],
                title=entry.get("title") or "",
                channel=entry.get("channel") or entry.get("uploader") or "",
                duration=entry.get("duration"),
            )
        )
    return videos


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def choose(track: Track, videos: list[Video], tolerance: float = TOLERANCE) -> Video | None:
    """The best video for `track`: within `tolerance` seconds of its duration; an
    "<Artist> - Topic" channel first, then the artist's own channel, then the closest
    duration (search order breaks ties). None if nothing is close enough."""
    if track.duration is None:
        return None
    artists = {_norm(a) for a in track.artists if a}

    def rank(item):
        order, video = item
        channel = video.channel.removesuffix(" - Topic")
        topic = video.channel.endswith(" - Topic") and _norm(channel) in artists
        own = _norm(channel) in artists
        return (not topic, not own, abs(video.duration - track.duration), order)

    close = [
        (i, v) for i, v in enumerate(videos)
        if v.duration is not None and abs(v.duration - track.duration) <= tolerance
    ]  # fmt: skip
    return min(close, key=rank)[1] if close else None


# --- cache and resolution ---------------------------------------------------------


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _from_entry(entry: dict) -> Match:
    return Match(Track(**entry["track"]), Video(**entry["video"]), entry["chosen_by"])


def cached_match(
    ident: str, folder: Path, now: float | None = None, *, stale: bool = False
) -> Match | None:
    """The saved match for track `ident`; None if there is none, or if it is older than
    MATCH_MAX_AGE (unless `stale`: then its age doesn't matter)."""
    entry = _read(folder / MATCHES).get(ident)
    if not entry:
        return None
    if not stale and (now or time.time()) - entry.get("saved", 0) > MATCH_MAX_AGE:
        return None
    try:
        return _from_entry(entry)
    except (KeyError, TypeError):
        return None


def remember(match: Match, folder: Path, now: float | None = None) -> None:
    matches = _read(folder / MATCHES)
    matches[match.track.id] = {
        "track": asdict(match.track),
        "video": asdict(match.video),
        "chosen_by": match.chosen_by,
        "saved": now or time.time(),
    }
    _write(folder / MATCHES, matches)


def forget(ident: str, folder: Path) -> None:
    matches = _read(folder / MATCHES)
    if matches.pop(ident, None) is not None:
        _write(folder / MATCHES, matches)


def match_track(
    link: str,
    folder: Path,
    *,
    video_link: str | None = None,
    refresh: bool = False,
    status: Callable[[str], None] | None = None,
    http: Callable = _http,
    search: Callable[[str], list[Video]] = search_youtube,
    video_info: Callable[[str], Video] | None = None,
) -> Match:
    """The YouTube video to analyse for a Spotify track link.

    `video_link`: the user's own choice of video (a YouTube link), which replaces
    whatever was matched before. `video_info(url)` looks up that video's details.
    """
    say = status or (lambda message: None)
    ident = track_id(link)
    saved = None if video_link is not None else cached_match(ident, folder, stale=True)
    # `refresh` redoes the search, but never throws away the user's own choice.
    if saved and (not refresh or saved.chosen_by == "user") and cached_match(ident, folder):
        say(
            f"Spotify: {saved.track.artist} - {saved.track.title} → YouTube: "
            f"{saved.video.title} (saved match)"
        )
        return saved
    # settings.json sits in the app's data folder, above cache/downloads
    track = fetch_track(ident, data_dir=folder.parent.parent, http=http)
    if video_link is not None or (saved and saved.chosen_by == "user"):
        # The user's video: newly given, or kept from before with fresh track details.
        video = (video_info or youtube_video)(video_link) if video_link is not None else saved.video
        match = Match(track, video, "user")
        remember(match, folder)
        say(f"Spotify: {track.artist} - {track.title} → your video: {video.title}")
        return match
    query = f"{track.artist} - {track.title}" if track.artist else track.title
    say(f"Spotify: {track.artist} - {track.title}; searching YouTube for the same recording")
    video = choose(track, search(query))
    if video is None:
        length = _mmss(track.duration) if track.duration else "unknown length"
        raise SpotifyMatchError(
            f"no confident match on YouTube for {track.artist} - {track.title} ({length}): "
            "no result within 3 seconds of the Spotify length. Paste a YouTube link of the "
            "song instead"
        )
    match = Match(track, video, "search")
    remember(match, folder)
    say(f"matched YouTube video: {video.title} ({video.channel}, {_mmss(video.duration)})")
    return match


def youtube_video(url: str) -> Video:
    """Details of one YouTube video (metadata only)."""
    import yt_dlp
    from yt_dlp.utils import DownloadError

    from chordchart.download import _Logger, classify

    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": _Logger(),
            "socket_timeout": 20, "extract_flat": True}  # fmt: skip
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as err:
        raise classify(err, url) from err
    if info.get("_type") == "playlist" or "entries" in info:
        raise InvalidLinkError(f"not a single-video link: {url}")
    return Video(
        id=info["id"],
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        duration=info.get("duration"),
    )


def _mmss(seconds: float | None) -> str:
    if seconds is None:
        return "?:??"
    seconds = round(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"
