"""chordchart with no source (prompts) and --clipboard."""

import io
import subprocess

import pytest

from chordchart import cli, interactive
from chordchart.errors import InputError

LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1"


@pytest.fixture
def seen(monkeypatch, sample_song):
    calls = {}

    def fake_analyze(source, **kw):
        calls.update(kw, source=source)
        return sample_song

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    return calls


def _typed(monkeypatch, text, tty=True):
    monkeypatch.setattr(interactive, "is_interactive", lambda: tty)
    monkeypatch.setattr(interactive.sys, "stdin", io.StringIO(text))


def test_prompts_for_link_start_and_end(monkeypatch, capsys, seen):
    _typed(monkeypatch, f'"{LINK}"\n1:05\n2:10\n')
    assert cli.main([]) == 0
    assert (seen["source"], seen["start"], seen["end"]) == (LINK, 65.0, 130.0)
    captured = capsys.readouterr()
    assert "Link or file: " in captured.err and "Start (Enter = beginning): " in captured.err
    assert "Link or file" not in captured.out  # prompts never land in the chart


def test_enter_means_whole_song(monkeypatch, seen):
    _typed(monkeypatch, f"{LINK}\n\n\n")
    assert cli.main([]) == 0
    assert (seen["start"], seen["end"]) == (0.0, None)


def test_empty_link_asks_again(monkeypatch, seen):
    _typed(monkeypatch, f"\n  \n{LINK}\n\n\n")
    assert cli.main([]) == 0
    assert seen["source"] == LINK


def test_bad_time_asks_again(monkeypatch, capsys, seen):
    _typed(monkeypatch, f"{LINK}\nabc\n1:75\n1:05\n\n")
    assert cli.main([]) == 0
    assert seen["start"] == 65.0
    err = capsys.readouterr().err
    assert "invalid time 'abc'" in err and "below 60" in err


def test_end_before_start_asks_again(monkeypatch, capsys, seen):
    _typed(monkeypatch, f"{LINK}\n2:00\n1:00\n2:30\n")
    assert cli.main([]) == 0
    assert (seen["start"], seen["end"]) == (120.0, 150.0)
    assert "the end must be after the start" in capsys.readouterr().err


def test_flags_are_not_asked_again(monkeypatch, capsys, seen):
    _typed(monkeypatch, f"{LINK}\n")
    assert cli.main(["--start", "10", "--end", "40"]) == 0
    assert (seen["start"], seen["end"]) == (10.0, 40.0)
    assert "Start (" not in capsys.readouterr().err


def test_end_of_input_exits_quietly(monkeypatch, seen):
    _typed(monkeypatch, "")
    assert cli.main([]) == interactive.INTERRUPTED
    assert "source" not in seen


def test_ctrl_c_exits_quietly(monkeypatch, seen):
    monkeypatch.setattr(interactive, "is_interactive", lambda: True)

    def interrupt(prompt):
        raise KeyboardInterrupt

    monkeypatch.setattr(interactive, "ask", interrupt)
    assert cli.main([]) == interactive.INTERRUPTED


def test_no_terminal_no_source_is_a_usage_error(monkeypatch, capsys, seen):
    _typed(monkeypatch, "", tty=False)
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    assert "run in a terminal to be prompted" in capsys.readouterr().err


def test_clipboard_flag(monkeypatch, capsys, seen):
    monkeypatch.setattr(interactive, "read_clipboard", lambda: LINK)
    assert cli.main(["--clipboard", "--start", "30"]) == 0
    assert (seen["source"], seen["start"], seen["end"]) == (LINK, 30.0, None)
    assert f"from clipboard: {LINK}" in capsys.readouterr().err


def test_clipboard_with_source_is_a_usage_error(seen):
    with pytest.raises(SystemExit) as exc:
        cli.main(["song.mp3", "--clipboard"])
    assert exc.value.code == 2


def test_clipboard_error_is_one_line(monkeypatch, capsys, seen):
    def empty():
        raise InputError("the clipboard is empty; copy the link first")

    monkeypatch.setattr(interactive, "read_clipboard", empty)
    assert cli.main(["--clipboard"]) == 2
    assert capsys.readouterr().err == "error: the clipboard is empty; copy the link first\n"


def _clipboard_holds(monkeypatch, text):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")

    monkeypatch.setattr(interactive.subprocess, "run", fake_run)


def test_read_clipboard_strips_quotes_and_newline(monkeypatch):
    _clipboard_holds(monkeypatch, f'"{LINK}"\r\n')
    assert interactive.read_clipboard() == LINK


@pytest.mark.parametrize(
    ("text", "message"),
    [("", "empty"), ("  \r\n", "empty"), ("line one\nline two\n", "several lines")],
)
def test_read_clipboard_rejects(monkeypatch, text, message):
    _clipboard_holds(monkeypatch, text)
    with pytest.raises(InputError, match=message):
        interactive.read_clipboard()


def test_read_clipboard_tool_missing(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(interactive.subprocess, "run", missing)
    with pytest.raises(InputError, match="could not read the clipboard"):
        interactive.read_clipboard()
