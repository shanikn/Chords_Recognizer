"""Stage timings of the chord pipeline as users run it: the CLI, --refresh (models rerun,
no analysis cache), stages overlapping as usual. Songs: common.TIMING_SONGS.

    uv run python experiments/timing/measure_stages.py [OUT_DIR]
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS, TIMING_SONGS, out_dir  # noqa: E402

STAGES = ["models", "decode", "beats", "chords", "key", "chart"]


def main():
    out = out_dir("timing")
    rows = []
    for name, file in TIMING_SONGS.items():
        result = out / f"stages-{file}.json"
        cmd = ["uv", "run", "chordchart", str(DOWNLOADS / file), "--refresh", "--format", "json"]
        subprocess.run([*cmd, "-o", str(result)], check=True, capture_output=True)
        song = json.loads(result.read_text(encoding="utf-8"))
        rows.append((name, song["duration"], song["timings"], song["elapsed"]))

    print(f"{'song':26} {'length':>6} " + " ".join(f"{s:>7}" for s in STAGES) + f" {'total':>7}")
    for name, duration, t, elapsed in rows:
        cells = " ".join(f"{t.get(s, 0):7.1f}" for s in STAGES)
        print(f"{name:26} {duration:6.0f} {cells} {elapsed:7.1f}")


if __name__ == "__main__":
    main()
