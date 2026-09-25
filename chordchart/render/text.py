"""Plain-text chord chart: a header, then 4 bars per line.

    Test Song
    Key: C major   Tempo: 120 BPM   Time: 4/4

    | C        | C G      | Am       | F        |

Everything the renderer itself writes is ASCII. Only the title (from the file name)
can contain other characters. The CLI deals with consoles that can't print them.
"""

from __future__ import annotations

from chordchart.model import Bar, Song, meter_label
from chordchart.timecode import format_time

BARS_PER_LINE = 4
CELL_WIDTH = 8


def render_text(song: Song) -> str:
    lines = [
        song.title,
        f"Key: {song.key}   Tempo: {round(song.bpm)} BPM   Time: {meter_label(song.meter)}",
    ]
    if song.section_start > 0 or song.section_end is not None:
        lines.append(_section_line(song))
    lines += [f"Note: {w}" for w in song.warnings]
    lines.append("")
    for i in range(0, len(song.bars), BARS_PER_LINE):
        row = song.bars[i : i + BARS_PER_LINE]
        lines.append("| " + " | ".join(_cell(bar) for bar in row) + " |")
    return "\n".join(lines) + "\n"


def _section_line(song: Song) -> str:
    """ "Section: 1:04.2-2:11 (requested 1:05-2:10)". The request is shown only when
    widening to whole bars changed it."""
    start = format_time(song.section_start)
    end_s = song.section_start + song.duration if song.section_end is None else song.section_end
    line = f"Section: {start}-{format_time(end_s)}"
    if song.requested_start is None:
        return line
    asked = format_time(song.requested_start)
    if song.requested_end is None:
        return line if asked == start else f"{line} (requested from {asked})"
    asked_end = format_time(song.requested_end)
    if (asked, asked_end) == (start, format_time(end_s)):
        return line
    return f"{line} (requested {asked}-{asked_end})"


def _cell(bar: Bar) -> str:
    return " ".join(c.symbol for c in bar.chords).ljust(CELL_WIDTH)
