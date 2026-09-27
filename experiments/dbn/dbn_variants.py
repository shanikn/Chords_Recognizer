"""Beat tracking split (RNN vs DBN), and narrower DBN settings, on every cached song.

For each song: decode as the pipeline does, compute the RNN activations once (timed,
saved to OUT/dbn/activations), then run the current DBN and each variant on the same
activations (timed), and compare their bar lines (downbeats) and meter with the current
settings, exactly and within 20/50 ms. The DBN runs single-threaded (3/4 then 4/4), so
only one Viterbi back-pointer table (frames x states x 4 bytes: ~2 GB for a 6-minute
song at the default settings) is in memory at a time. chordchart itself isn't changed.

    uv run python experiments/dbn/dbn_variants.py [OUT_DIR]

Result (2026-09-27, 11 songs): no variant kept the bar lines on every song, even within
50 ms (best: 60-190 BPM, 60 tempi, 8/11); rejected. See experiments/README.md.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import cached_songs, out_dir  # noqa: E402

from chordchart.beats import FPS  # noqa: E402
from chordchart.fetch import decode_section, load_signal  # noqa: E402
from chordchart.pipeline import DEFAULT_THREADS  # noqa: E402

CURRENT = "current (55-205, 60 tempi)"
# madmom's defaults: min_bpm=55, max_bpm=205, num_tempi=60, transition_lambda=100.
VARIANTS = {
    CURRENT: {},
    "60-190, 60 tempi": {"min_bpm": 60, "max_bpm": 190},
    "55-205, 40 tempi": {"num_tempi": 40},
    "55-205, 30 tempi": {"num_tempi": 30},
    "60-190, 40 tempi": {"min_bpm": 60, "max_bpm": 190, "num_tempi": 40},
    "70-180, 30 tempi": {"min_bpm": 70, "max_bpm": 180, "num_tempi": 30},
}


def run(out: Path) -> dict:
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor, RNNDownBeatProcessor

    activations_dir = out / "activations"
    activations_dir.mkdir(exist_ok=True)
    rnn = RNNDownBeatProcessor(num_threads=DEFAULT_THREADS)
    dbns = {
        name: DBNDownBeatTrackingProcessor(beats_per_bar=[3, 4], fps=FPS, num_threads=1, **kwargs)
        for name, kwargs in VARIANTS.items()
    }
    results = {}
    for src in cached_songs():
        cached = activations_dir / f"{src.stem}.npy"
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            decoded = decode_section(src, wav)
            began = time.perf_counter()
            if cached.exists():
                activations, rnn_s = np.load(cached), None
            else:
                activations = rnn(load_signal(wav))
                rnn_s = time.perf_counter() - began
                np.save(cached, activations)
        song = {"duration": decoded.duration, "rnn_s": rnn_s, "variants": {}}
        song["states"] = {
            n: [h.transition_model.num_states for h in d.hmms] for n, d in dbns.items()
        }
        for name, dbn in dbns.items():
            began = time.perf_counter()
            tracked = np.asarray(dbn(activations)).reshape(-1, 2)
            song["variants"][name] = {
                "seconds": time.perf_counter() - began,
                "times": tracked[:, 0].tolist(),
                "positions": tracked[:, 1].astype(int).tolist(),
            }
        results[src.stem] = song
        times = {n: round(v["seconds"], 1) for n, v in song["variants"].items()}
        print(
            f"{src.stem} {decoded.duration:.0f}s rnn={rnn_s and round(rnn_s, 1)}s dbn={times}",
            flush=True,
        )
    (out / "dbn_results.json").write_text(json.dumps(results))
    return results


def _downbeats(variant: dict) -> list[float]:
    return [t for t, p in zip(variant["times"], variant["positions"], strict=True) if p == 1]


def compare(results: dict) -> None:
    names = [n for n in VARIANTS if n != CURRENT]
    print(
        f"\n{'variant':20} {'identical bars':>15} {'<=20ms':>7} {'<=50ms':>7} "
        f"{'DBN time':>9} {'4/4 states':>11}"
    )
    for name in names:
        exact = within20 = within50 = 0
        t_new = t_old = 0.0
        for song in results.values():
            a, b = _downbeats(song["variants"][CURRENT]), _downbeats(song["variants"][name])
            same_count = len(a) == len(b)
            gap = max((abs(x - y) for x, y in zip(a, b, strict=False)), default=0.0)
            exact += a == b
            within20 += same_count and gap <= 0.02 + 1e-9
            within50 += same_count and gap <= 0.05 + 1e-9
            t_new += song["variants"][name]["seconds"]
            t_old += song["variants"][CURRENT]["seconds"]
        n = len(results)
        states = next(iter(results.values()))["states"][name][1]
        print(
            f"{name:20} {exact:>9}/{n:<5} {within20:>3}/{n:<3} {within50:>3}/{n:<3} "
            f"{t_new / t_old:>8.0%} {states:>11}"
        )


if __name__ == "__main__":
    out = out_dir("dbn")
    compare(run(out))
