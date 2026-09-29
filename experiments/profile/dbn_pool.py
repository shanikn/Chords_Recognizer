"""madmom's DBN for Beat This!: 3/4 and 4/4 in a worker pool (2 processes) vs one after
the other in-process. Same activations for both; checks the beats are identical, and
measures time and peak memory of the whole process tree (the pool's workers included),
each mode in a fresh process.

    uv run --with psutil python experiments/profile/dbn_pool.py [SONG_FILE]
"""

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

SONG = DOWNLOADS / "youtube-20iOlPwz0J0.webm"  # the longest cached song, 5:52


def tree_rss(ps) -> int:
    import psutil

    total = ps.memory_info().rss
    for child in ps.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            pass
    return total


def worker(mode: str, song: str, activations_file: str) -> None:
    import psutil

    from chordchart import beat_this
    from chordchart.processors import _find_pools

    activations = np.load(activations_file)
    me = psutil.Process()
    base = tree_rss(me)
    peak = [base]
    done = threading.Event()

    def sample():
        while not done.is_set():
            peak[0] = max(peak[0], tree_rss(me))
            time.sleep(0.01)

    threading.Thread(target=sample, daemon=True).start()
    began = time.perf_counter()
    dbn = beat_this.make_dbn((3, 4), threads=2 if mode == "pool" else 1)
    built = time.perf_counter()
    beats = np.asarray(dbn(activations))
    ended = time.perf_counter()
    done.set()
    for pool in _find_pools([dbn]):
        pool.terminate()
    mb = 2**20
    print(json.dumps({
        "mode": mode, "build_s": round(built - began, 2), "decode_s": round(ended - built, 2),
        "total_s": round(ended - began, 2), "tree_before_mb": base // mb, "tree_peak_mb": peak[0] // mb,
        "extra_mb": (peak[0] - base) // mb, "beats": beats.tolist(),
    }))  # fmt: skip


def main() -> None:
    from chordchart import beat_this
    from chordchart.beats import _samples
    from chordchart.fetch import decode_section, load_signal

    song = Path(sys.argv[1]) if len(sys.argv) > 1 else SONG
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(song, wav)
        samples = _samples(load_signal(wav))
        activations = beat_this.dbn_activations(*beat_this.BeatThisModel(threads=4).logits(samples))
        act_file = Path(tmp) / "act.npy"
        np.save(act_file, activations)
        results = {}
        for mode in ("pool", "sequential", "pool", "sequential"):
            out = subprocess.run(
                [sys.executable, __file__, "--worker", mode, str(song), str(act_file)],
                capture_output=True, text=True, check=True,
            )  # fmt: skip
            row = json.loads(out.stdout.strip().splitlines()[-1])
            results.setdefault(mode, []).append(row)
    beats = {mode: rows[0]["beats"] for mode, rows in results.items()}
    print(f"{song.name}: {len(activations) / 100:.0f} s of audio, {len(beats['pool'])} beats")
    print("identical beats and downbeats:", beats["pool"] == beats["sequential"]
          and all(r["beats"] == beats["pool"] for rows in results.values() for r in rows))  # fmt: skip
    for mode, rows in results.items():
        for r in rows:
            print(f"  {mode:10} build {r['build_s']:5.2f} s  decode {r['decode_s']:5.2f} s  "
                  f"total {r['total_s']:5.2f} s  tree peak {r['tree_peak_mb']} MB (+{r['extra_mb']} MB)")  # fmt: skip


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(*sys.argv[2:5])
    else:
        main()
