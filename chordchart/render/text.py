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
        end = song.section_start + song.duration if song.section_end is None else song.section_end
        lines.append(f"Section: {format_time(song.section_start)}-{format_time(end)}")
    lines += [f"Note: {w}" for w in song.warnings]
    lines.append("")
    for i in range(0, len(song.bars), BARS_PER_LINE):
        row = song.bars[i : i + BARS_PER_LINE]
        lines.append("| " + " | ".join(_cell(bar) for bar in row) + " |")
    return "\n".join(lines) + "\n"


def _cell(bar: Bar) -> str:
    return " ".join(c.symbol for c in bar.chords).ljust(CELL_WIDTH)
