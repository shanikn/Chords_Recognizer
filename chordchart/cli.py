"""Command line: chordchart <audio-file> [--format txt|json] [-o FILE]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from chordchart.errors import ChordChartError
from chordchart.fetch import DEFAULT_MAX_DURATION
from chordchart.pipeline import analyze
from chordchart.render.text import render_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chordchart",
        description="Detect the chords of a song and print a bar-aligned chord chart.",
    )
    parser.add_argument("source", help="path to an audio or video file")
    parser.add_argument("--format", choices=["txt", "json"], default="txt")
    parser.add_argument("-o", "--output", type=Path, help="write to FILE instead of stdout")
    parser.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAX_DURATION / 60,
        metavar="MINUTES",
        help="refuse longer audio (default: %(default)g)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        song = analyze(args.source, max_duration=args.max_duration * 60)
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
