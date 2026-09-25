import io
import json

from chordchart import cli
from chordchart.errors import AudioRejectedError


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
