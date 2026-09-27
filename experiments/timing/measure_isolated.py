"""Each pipeline stage on its own: parallel=False, no analysis cache, models loaded once.
In a normal run beats, chords and key share the CPU, so their times can't be compared
directly; here they can. Songs: common.TIMING_SONGS.

    uv run python experiments/timing/measure_isolated.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS, TIMING_SONGS  # noqa: E402

from chordchart.pipeline import DEFAULT_THREADS, analyze  # noqa: E402
from chordchart.processors import Processors  # noqa: E402

STAGES = ["decode", "beats", "chords", "key", "chart"]


def main():
    with Processors(num_threads=DEFAULT_THREADS) as processors:
        print(
            f"{'song':26} {'length':>6} " + " ".join(f"{s:>7}" for s in STAGES) + "   beats share"
        )
        for name, file in TIMING_SONGS.items():
            song = analyze(DOWNLOADS / file, processors=processors, parallel=False, cache=False)
            t = song.timings
            share = t["beats"] / sum(t.get(s, 0) for s in STAGES)
            cells = " ".join(f"{t.get(s, 0):7.1f}" for s in STAGES)
            print(f"{name:26} {song.duration:6.0f} {cells}   {share:5.0%}", flush=True)


if __name__ == "__main__":
    main()
