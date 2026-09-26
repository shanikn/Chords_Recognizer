"""Getting the source without typing it on the command line.

Pasting a YouTube link into PowerShell breaks it at `&` (a command separator), so the
CLI can prompt for it instead, or read it from the clipboard. Prompts go to stderr, so
`chordchart > chart.txt` never captures them.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from chordchart.errors import InputError
from chordchart.timecode import parse_time

INTERRUPTED = 130  # conventional exit code for Ctrl+C


@dataclass(frozen=True)
class Answers:
    source: str
    start: float
    end: float | None


def is_interactive() -> bool:
    return sys.stdin.isatty()


def ask(prompt: str) -> str:
    """One line from stdin, prompt on stderr. EOF (Ctrl+Z/Ctrl+D) raises EOFError."""
    print(prompt, end="", file=sys.stderr, flush=True)
    line = sys.stdin.readline()
    if line == "":
        raise EOFError
    return line.strip()


def prompt_for_source(start: float | None, end: float | None) -> Answers:
    """Ask for the link or file, then for start/end unless they were given as flags.

    Enter at a time prompt means "the beginning" / "the end of the song". A bad time
    or an end before the start asks again.
    """
    source = ""
    while not source:
        source = unquote(ask("Link or file: "))
    if start is None:
        start = _ask_time("Start (Enter = beginning): ", default=0.0)
    if end is None:
        while True:
            end = _ask_time("End (Enter = end of song): ", default=None)
            if end is None or end > start:
                break
            print("  the end must be after the start", file=sys.stderr)
    return Answers(source, start, end)


def _ask_time(prompt: str, default: float | None) -> float | None:
    while True:
        text = ask(prompt)
        if not text:
            return default
        try:
            return parse_time(text)
        except ValueError as exc:
            print(f"  {exc}", file=sys.stderr)


def read_clipboard() -> str:
    """The clipboard's text: PowerShell Get-Clipboard on Windows, pbpaste on macOS."""
    if sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard"]
    elif sys.platform == "darwin":
        cmd = ["pbpaste"]
    else:
        cmd = ["xclip", "-selection", "clipboard", "-o"]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InputError(f"could not read the clipboard: {exc}") from None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise InputError("the clipboard is empty; copy the link first")
    if len(lines) > 1:
        raise InputError("the clipboard holds several lines; copy just the link")
    return unquote(lines[0])


def unquote(text: str) -> str:
    """Strip one pair of matching quotes, as left by some copy/paste paths."""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1].strip()
    return text
