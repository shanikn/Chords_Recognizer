"""Best-of-N stage times and peak memory from interleaved profile_pipeline.py runs.

The input is the runs' JSON lines, each run preceded by a "== <mode> run <n>" line:

    uv run python experiments/profile/summarize_ab.py AB_OUTPUT
"""

import json
import sys
from collections import defaultdict

STAGES = ("decode", "beats", "chords", "key", "chart")


def main():
    best = defaultdict(dict)  # (mode, song) -> {stage: best seconds}
    memory = defaultdict(dict)
    loads = defaultdict(list)
    mode = None
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        line = line.strip()
        if line.startswith("=="):
            mode = line.split()[1]
            continue
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event["kind"] == "analysis":
            key = (mode, event["song"], event["run"])
            values = {**event["timings"], "total": event["elapsed"]}
            for stage, seconds in values.items():
                old = best[key].get(stage)
                best[key][stage] = seconds if old is None else min(old, seconds)
        elif event["kind"] == "memory":
            for phase, mb in event["peak_mb_by_phase"].items():
                old = memory[mode].get(phase)
                memory[mode][phase] = mb if old is None else min(old, mb)
        elif event["kind"] == "time" and "Processors" in event["what"]:
            loads[mode].append(event["seconds"])
    modes = sorted({k[0] for k in best})
    songs = list(dict.fromkeys(k[1] for k in best))
    for song in songs:
        print(f"\n{song}")
        for stage in (*STAGES, "total"):
            cells = [best.get((m, song, "first"), {}).get(stage) for m in modes]
            print(f"  {stage:7}" + "".join(f"{m:>16}: {c:6.2f}" if c is not None else f"{m:>16}:      -" for m, c in zip(modes, cells, strict=True)))  # fmt: skip
        repeat = [best.get((m, song, "repeat"), {}).get("total") for m in modes]
        print("  repeat " + "".join(f"{m:>16}: {c:6.2f}" for m, c in zip(modes, repeat, strict=True)))
        peaks = [memory[m].get(f"analyze {song} (models run)") for m in modes]
        print("  peak MB" + "".join(f"{m:>16}: {c:6}" for m, c in zip(modes, peaks, strict=True)))
    for m in modes:
        print(f"model load {m}: best {min(loads[m]):.2f} s")


if __name__ == "__main__":
    main()
