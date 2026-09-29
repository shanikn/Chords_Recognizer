"""Beat This! (onnxruntime) time by thread count and session options, one song.

    uv run python experiments/profile/beat_threads.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

from chordchart import beat_this  # noqa: E402
from chordchart.beats import _samples  # noqa: E402
from chordchart.fetch import decode_section, load_signal  # noqa: E402


def main():
    import psutil

    print("logical", os.cpu_count(), "physical", psutil.cpu_count(logical=False))
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(DOWNLOADS / "youtube-hLQl3WQQoQ0.webm", wav)
        samples = _samples(load_signal(wav))
    reference = None
    for threads in (1, 2, 4, 6, 8, os.cpu_count()):
        model = beat_this.BeatThisModel(threads=threads)
        model.logits(samples[: 44100 * 20])  # warm-up
        t = time.perf_counter()
        out = model.logits(samples)
        took = time.perf_counter() - t
        if reference is None:
            reference = out
        diff = max(float(np.abs(a - b).max()) for a, b in zip(out, reference, strict=True))
        print(f"threads {threads:2}: {took:5.2f} s  (max diff vs 1 thread {diff:.1e})", flush=True)


if __name__ == "__main__":
    main()
