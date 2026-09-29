"""Errors that are shown to the user as a one-line message, not a traceback."""


class ChordChartError(Exception):
    exit_code = 1


class FfmpegNotFoundError(ChordChartError):
    def __init__(self) -> None:
        super().__init__(
            "ffmpeg was not found on PATH. Install it with: "
            "winget install Gyan.FFmpeg  (then open a new terminal)"
        )


class AudioDecodeError(ChordChartError):
    exit_code = 2


class AudioRejectedError(ChordChartError):
    exit_code = 2


# Link errors (download.py). One class per thing the user can do something about.


class InvalidLinkError(ChordChartError):
    """Unsupported site, malformed link, playlist/feed page or live stream."""

    exit_code = 2


class VideoUnavailableError(ChordChartError):
    """Private, removed, age-restricted, geo-blocked or sign-in required."""

    exit_code = 2


class NetworkError(ChordChartError):
    exit_code = 2


class InputError(ChordChartError):
    """The source couldn't be read (e.g. an empty clipboard)."""

    exit_code = 2


class CacheError(ChordChartError):
    """`chordchart cache clear` refused to touch a folder that isn't ours."""

    exit_code = 2


class DownloadFailedError(ChordChartError):
    """Anything else yt-dlp reports, usually fixed by upgrading yt-dlp."""

    exit_code = 2


class SpotifyMatchError(ChordChartError):
    """A Spotify track we couldn't read, or couldn't find on YouTube with confidence."""

    exit_code = 2
