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
