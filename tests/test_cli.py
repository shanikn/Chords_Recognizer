import io
import json

import pytest

from chordchart import cli
from chordchart.errors import (
    AudioRejectedError,
    DownloadFailedError,
    InvalidLinkError,
    NetworkError,
    VideoUnavailableError,
)


def test_prints_text_chart(monkeypatch, capsys, sample_song):
    monkeypatch.setattr(cli, "analyze", lambda source, **kw: sample_song)
    assert cli.main(["song.mp3"]) == 0
    assert capsys.readouterr().out.startswith("Test Song\nKey: C major")


def test_json_format(monkeypatch, capsys, sample_song):
    monkeypatch.setattr(cli, "analyze", lambda source, **kw: sample_song)
    assert cli.main(["song.mp3", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Test Song"


def test_output_file(monkeypatch, tmp_path, sample_song):
    monkeypatch.setattr(cli, "analyze", lambda source, **kw: sample_song)
    out = tmp_path / "chart.txt"
    assert cli.main(["song.mp3", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("Test Song")


def test_output_file_keeps_non_ascii_title(monkeypatch, tmp_path, sample_song):
    sample_song.title = "Café ♪"
    monkeypatch.setattr(cli, "analyze", lambda source, **kw: sample_song)
    out = tmp_path / "chart.txt"
    assert cli.main(["song.mp3", "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("Café ♪\n")


def test_console_that_cannot_encode_the_title_does_not_crash(monkeypatch, sample_song):
    # A Windows console redirected to a file uses cp1252, which has no "♪".
    sample_song.title = "Café ♪"
    monkeypatch.setattr(cli, "analyze", lambda source, **kw: sample_song)
    raw = io.BytesIO()
    monkeypatch.setattr(cli.sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    assert cli.main(["song.mp3"]) == 0
    cli.sys.stdout.flush()
    # "é" exists in cp1252 and is kept; "♪" doesn't and becomes "?". Compare the first
    # line only, because a text stream on Windows writes CRLF line endings.
    assert raw.getvalue().decode("cp1252").splitlines()[0] == "Café ?"


def test_max_duration_is_passed_in_seconds(monkeypatch, sample_song):
    seen = {}

    def fake_analyze(source, **kw):
        seen.update(kw)
        return sample_song

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    cli.main(["song.mp3", "--max-duration", "20"])
    assert seen["max_duration"] == 1200.0


def test_user_errors_are_one_line(monkeypatch, capsys):
    def boom(source, **kw):
        raise AudioRejectedError("audio is silent")

    monkeypatch.setattr(cli, "analyze", boom)
    assert cli.main(["song.mp3"]) == 2
    err = capsys.readouterr().err
    assert err == "error: audio is silent\n"


def _capture_analyze(monkeypatch, song):
    seen = {}

    def fake_analyze(source, **kw):
        seen.update(kw, source=source)
        return song

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    return seen


def test_section_and_refresh_reach_analyze(monkeypatch, sample_song):
    seen = _capture_analyze(monkeypatch, sample_song)
    url = "https://youtu.be/abc"
    assert cli.main([url, "--start", "1:05", "--end", "2:10", "--refresh"]) == 0
    assert (seen["source"], seen["start"], seen["end"], seen["refresh"]) == (url, 65.0, 130.0, True)


def test_defaults_reach_analyze(monkeypatch, sample_song):
    seen = _capture_analyze(monkeypatch, sample_song)
    cli.main(["song.mp3"])
    assert (seen["start"], seen["end"], seen["refresh"]) == (0.0, None, False)


def test_bad_time_is_a_usage_error(monkeypatch, capsys, sample_song):
    _capture_analyze(monkeypatch, sample_song)
    with pytest.raises(SystemExit) as exc:
        cli.main(["song.mp3", "--start", "1:75"])
    assert exc.value.code == 2
    assert "minutes and seconds must be below 60" in capsys.readouterr().err


def test_end_before_start_is_a_usage_error(monkeypatch, capsys, sample_song):
    _capture_analyze(monkeypatch, sample_song)
    with pytest.raises(SystemExit) as exc:
        cli.main(["song.mp3", "--start", "2:00", "--end", "1:00"])
    assert exc.value.code == 2
    assert "--end must be after --start" in capsys.readouterr().err


@pytest.mark.parametrize(
    "error",
    [
        InvalidLinkError("not a single-video link: https://x.com/ (unsupported site)"),
        VideoUnavailableError("video unavailable: Private video"),
        NetworkError("network error: could not reach www.youtube.com; check your connection"),
        DownloadFailedError("download failed: boom. YouTube changes often; try: ..."),
    ],
)
def test_link_errors_are_one_line(monkeypatch, capsys, error):
    def boom(source, **kw):
        raise error

    monkeypatch.setattr(cli, "analyze", boom)
    assert cli.main(["https://youtu.be/abc"]) == 2
    captured = capsys.readouterr()
    assert captured.err == f"error: {error}\n"
    assert captured.out == ""


def test_status_goes_to_stderr_not_stdout(monkeypatch, capsys, sample_song):
    def fake_analyze(source, **kw):
        kw["status"]("downloading: My Song (3:45)")
        return sample_song

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    assert cli.main(["https://youtu.be/abc"]) == 0
    captured = capsys.readouterr()
    assert captured.err == "downloading: My Song (3:45)\n"
    assert "downloading" not in captured.out
