"""Beat This! small0 (numpy + onnxruntime, minimal postprocessing) vs madmom as the
pipeline runs it, on every cached song: speed, and agreement of beats, downbeats (bar
lines) and meter with madmom. madmom is not ground truth: see evaluate/ for that.

    uv run python experiments/beat_this/benchmark.py

Result (2026-09-27, 11 songs): 3.3x faster (106 s vs 349 s); vs madmom: beat F 0.838,
downbeat F 0.856 (mean, +-70 ms), same meter 9/11, same bar lines 1/11.
"""

import json
import sys
import tempfile
import time
from collections import Counter
from itertools import pairwise
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import beat_this_onnx as bt  # noqa: E402
from common import cached_songs, out_dir  # noqa: E402

from chordchart.beats import track_beats  # noqa: E402
from chordchart.fetch import decode_section, load_signal, read_wav  # noqa: E402
from chordchart.pipeline import DEFAULT_THREADS  # noqa: E402
from chordchart.processors import Processors  # noqa: E402

TOL = 0.07


def f_measure(ref, est, tol=TOL) -> float:
    """One-to-one matches within +-tol (as mir_eval.beat.f_measure, without its trimming)."""
    ref, est = np.asarray(ref), np.asarray(est)
    if not len(ref) or not len(est):
        return 0.0
    used, hits = set(), 0
    for r in ref:
        j = int(np.argmin(np.abs(est - r)))
        if abs(est[j] - r) <= tol and j not in used:
            used.add(j)
            hits += 1
    return 2 * hits / (len(ref) + len(est))


def meter(beats, downs) -> int:
    lengths = [b - a for a, b in pairwise(np.searchsorted(beats, downs))]
    return int(Counter(lengths).most_common(1)[0][0]) if lengths else 0


def main():
    out = out_dir("beat_this")
    session = bt.session(out / "beat_this_small0.onnx", DEFAULT_THREADS)
    rows = []
    with Processors(num_threads=DEFAULT_THREADS) as processors:
        for src in cached_songs():
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "a.wav"
                decoded = decode_section(src, wav)
                signal = load_signal(wav)
                began = time.perf_counter()
                mm = track_beats(signal, (3, 4), processors)
                t_mm = time.perf_counter() - began
                samples = read_wav(wav)
            began = time.perf_counter()
            b_beats, b_downs = bt.beats_downbeats(samples, session)
            t_bt = time.perf_counter() - began
            m_beats = np.array(mm.times)
            m_downs = np.array([t for t, p in zip(mm.times, mm.positions, strict=True) if p == 1])
            same_bars = len(m_downs) == len(b_downs) and bool(
                np.all(np.abs(m_downs - b_downs) <= TOL)
            )
            row = {
                "song": src.stem, "length": decoded.duration, "madmom_s": t_mm, "beatthis_s": t_bt,
                "beat_f": f_measure(m_beats, b_beats), "down_f": f_measure(m_downs, b_downs),
                "same_bars": same_bars, "meter": [mm.meter, meter(b_beats, b_downs)],
            }  # fmt: skip
            rows.append(row)
            print(
                f"{row['song']:22} {row['length']:5.0f}s  madmom {t_mm:5.1f}s  "
                f"beat-this {t_bt:5.1f}s  beat F {row['beat_f']:.3f}  "
                f"downbeat F {row['down_f']:.3f}  same bars {same_bars}  meter {row['meter']}",
                flush=True,
            )
    (out / "benchmark.json").write_text(json.dumps(rows, indent=1))
    mm_total = sum(r["madmom_s"] for r in rows)
    bt_total = sum(r["beatthis_s"] for r in rows)
    print(
        f"\ntotal: madmom {mm_total:.0f} s, beat-this {bt_total:.0f} s "
        f"({mm_total / bt_total:.1f}x); "
        f"mean beat F {np.mean([r['beat_f'] for r in rows]):.3f}, "
        f"mean downbeat F {np.mean([r['down_f'] for r in rows]):.3f}, "
        f"same bar lines {sum(r['same_bars'] for r in rows)}/{len(rows)}, "
        f"same meter {sum(r['meter'][0] == r['meter'][1] for r in rows)}/{len(rows)}"
    )


if __name__ == "__main__":
    main()
