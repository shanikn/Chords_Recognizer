"""Command line.

chordchart <file-or-link> [--start T] [--end T] [--format txt|json] [-o FILE] ...
chordchart                  prompts for the link, then start/end
chordchart --clipboard      reads the link from the clipboard
chordchart cache info | clear
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chordchart import interactive
from chordchart.download import CacheSummary, cache_summary, clear_cache, default_cache_dir
from chordchart.errors import ChordChartError
from chordchart.fetch import DEFAULT_MAX_DURATION
from chordchart.pipeline import analyze
from chordchart.render.text import render_text
from chordchart.timecode import parse_time


def _downloads_dir() -> Path:
    return default_cache_dir() / "downloads"


def _time(text: str) -> float:
    try:
        return parse_time(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chordchart",
        description="Detect the chords of a song and print a bar-aligned chord chart.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Downloads from links are cached in:\n  {_downloads_dir()}\n"
            "Set CHORDCHART_CACHE_DIR to use another folder.\n"
            "Manage the cache with: chordchart cache info | chordchart cache clear"
        ),
    )
    parser.add_argument(
        "source",
        nargs="?",
        help=(
            "audio or video file, or a video link (YouTube and other sites yt-dlp "
            "supports). Omit it to be prompted, which is easier for links containing '&'"
        ),
    )
    parser.add_argument("--clipboard", action="store_true", help="read the link from the clipboard")
    parser.add_argument(
        "--start",
        type=_time,
        default=None,
        metavar="TIME",
        help="analyse from TIME (SS, MM:SS or H:MM:SS)",
    )
    parser.add_argument(
        "--end",
        type=_time,
        default=None,
        metavar="TIME",
        help="analyse up to TIME",
    )
    parser.add_argument("--format", choices=["txt", "json"], default="txt")
    parser.add_argument("-o", "--output", type=Path, help="write to FILE instead of stdout")
    parser.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAX_DURATION / 60,
        metavar="MINUTES",
        help="refuse longer audio or videos (default: %(default)g)",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="download a link again even if it is cached"
    )
    return parser


def build_cache_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chordchart cache",
        description=f"Manage downloaded audio in {_downloads_dir()}",
    )
    parser.add_argument(
        "action",
        choices=["info", "clear"],
        help="info: show location and size; clear: delete all downloads",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["cache"]:  # a file literally named "cache" can be passed as ./cache
        return cache_main(argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.end is not None and args.end <= (args.start or 0.0):
        parser.error("--end must be after --start")

    start, end = args.start, args.end
    try:
        if args.clipboard:
            if args.source is not None:
                parser.error("--clipboard can't be combined with a source argument")
            args.source = interactive.read_clipboard()
            print(f"from clipboard: {args.source}", file=sys.stderr)
        elif args.source is None:
            if not interactive.is_interactive():
                parser.error("a source is required (or run in a terminal to be prompted)")
            answers = interactive.prompt_for_source(start, end)
            args.source, start, end = answers.source, answers.start, answers.end
    except (KeyboardInterrupt, EOFError):
        print(file=sys.stderr)
        return interactive.INTERRUPTED
    except ChordChartError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    try:
        song = analyze(
            args.source,
            max_duration=args.max_duration * 60,
            start=start or 0.0,
            end=end,
            refresh=args.refresh,
            status=lambda message: print(message, file=sys.stderr),
        )
    except ChordChartError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    text = render_text(song) if args.format == "txt" else song.to_json() + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        # A Windows console redirected to a file encodes as cp1252. A title it can't
        # represent (from the file name) prints as "?" instead of crashing.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        sys.stdout.write(text)
    return 0


def cache_main(argv: list[str]) -> int:
    args = build_cache_parser().parse_args(argv)
    folder = _downloads_dir()
    try:
        if args.action == "info":
            summary = cache_summary(folder)
            print(f"cache: {folder}\n{_describe(summary)}")
        else:
            removed = clear_cache(folder)
            if removed.downloads == 0 and removed.size == 0:
                print(f"cache is empty: {folder}")
            else:
                print(f"removed {_describe(removed)} from {folder}")
    except ChordChartError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    return 0


def _describe(summary: CacheSummary) -> str:
    noun = "download" if summary.downloads == 1 else "downloads"
    return f"{summary.downloads} {noun} ({summary.size / 1e6:.1f} MB)"
