"""`chordchart notes`: transcribe the main instrument to a MIDI file (or JSON).

Kept apart from chordchart.cli so the chord command never imports torch.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from chordchart.cli import _print_status, _time
from chordchart.errors import ChordChartError
from chordchart.fetch import DEFAULT_MAX_DURATION
from chordchart.notes.available import MISSING, notes_available
from chordchart.notes.model import INSTRUMENTS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chordchart notes",
        description=(
            "Transcribe the notes of the song's main instrument (the loudest of bass, "
            "guitar, piano and other; vocals and drums are left out) to MIDI. "
            "Separating the instruments takes about twice the audio's length."
        ),
    )
    parser.add_argument("source", help="audio or video file, or a video link")
    parser.add_argument("--start", type=_time, default=0.0, metavar="TIME")
    parser.add_argument("--end", type=_time, default=None, metavar="TIME")
    parser.add_argument(
        "--instrument", choices=INSTRUMENTS, help="transcribe this stem instead of the loudest"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="MIDI file to write, or a .json file for the notes as JSON "
        "(default: <song title>.mid in the current folder)",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=DEFAULT_MAX_DURATION / 60,
        metavar="MINUTES",
        help="refuse longer audio or videos (default: %(default)g)",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="separate the instruments again (skip the cache)"
    )
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.end is not None and args.end <= args.start:
        parser.error("--end must be after --start")
    if not notes_available():
        print(f"error: {MISSING}", file=sys.stderr)
        return 2

    from chordchart.notes.midi import to_midi
    from chordchart.notes.pipeline import transcribe_notes

    shown = [-1]

    def progress(fraction: float) -> None:
        tenth = int(fraction * 10)
        if tenth > shown[0]:
            shown[0] = tenth
            print(f"  separating instruments: {tenth * 10}%", file=sys.stderr, flush=True)

    try:
        result = transcribe_notes(
            args.source,
            start=args.start,
            end=args.end,
            instrument=args.instrument,
            max_duration=args.max_duration * 60,
            refresh=args.refresh,
            status=_print_status,
            progress=progress,
        )
    except ChordChartError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    levels = ", ".join(f"{name} {db:.0f} dB" for name, db in result.levels.items())
    how = "loudest" if result.automatic else "chosen"
    print(f"instrument: {result.instrument} ({how}; levels: {levels})", file=sys.stderr)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"total: {result.elapsed:.1f} s", file=sys.stderr)

    output = args.output or Path(f"{_file_name(result.title)}.mid")
    if output.suffix.lower() == ".json":
        output.write_text(result.to_json() + "\n", encoding="utf-8")
    else:
        output.write_bytes(to_midi(result))
    print(f"{len(result.notes)} notes written to {output}", file=sys.stderr)
    return 0


def _file_name(title: str) -> str:
    """The title, minus characters Windows doesn't allow in file names."""
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .") or "notes"
