"""Beat This! on onnxruntime: CPU memory arena kept (onnxruntime's default), off, shrunk
after every run, or shrunk after a song's last chunk (what chordchart.beat_this does).
Time for one song, peak and retained memory, and whether the logits are identical.
Each mode in a fresh process, interleaved, twice.

    uv run --with psutil python experiments/profile/ort_arena.py
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

SONG = DOWNLOADS / "youtube-20iOlPwz0J0.webm"
MODES = ("arena", "no-arena", "shrink", "shrink-last")


def worker(mode: str, samples_file: str, out_file: str) -> None:
    import onnxruntime as ort
    import psutil

    from chordchart import beat_this

    samples = np.load(samples_file)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    if mode == "no-arena":
        options.enable_cpu_mem_arena = False
    model = beat_this.BeatThisModel(threads=4)
    model.session = ort.InferenceSession(
        str(beat_this.MODEL), options, providers=["CPUExecutionProvider"]
    )
    if mode != "shrink-last":
        model._release = None  # never shrink after the last chunk
    if mode == "shrink":
        run_options = ort.RunOptions()
        run_options.add_run_config_entry("memory.enable_memory_arena_shrinkage", "cpu:0")
        session_run = model.session.run
        model.session.run = lambda outputs, feeds, _options=None: session_run(
            outputs, feeds, run_options
        )
    me = psutil.Process()
    peak = [0]
    done = threading.Event()

    def sample():
        while not done.is_set():
            peak[0] = max(peak[0], me.memory_info().rss)
            time.sleep(0.005)

    threading.Thread(target=sample, daemon=True).start()
    times = []
    for _ in range(2):  # a first and a second song in the same process
        began = time.perf_counter()
        beat, down = model.logits(samples)
        times.append(round(time.perf_counter() - began, 2))
    done.set()
    np.savez(out_file, beat=beat, down=down)
    print(json.dumps({"mode": mode, "times": times, "peak_mb": peak[0] >> 20,
                      "after_mb": me.memory_info().rss >> 20}))  # fmt: skip


def main() -> None:
    from chordchart.beats import _samples
    from chordchart.fetch import decode_section, load_signal

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(SONG, wav)
        samples_file = Path(tmp) / "s.npy"
        np.save(samples_file, _samples(load_signal(wav)))
        rows, logits = [], {}
        for _ in range(2):
            for mode in MODES:
                out = Path(tmp) / f"{mode}.npz"
                command = [sys.executable, __file__, "--worker", mode, str(samples_file), str(out)]
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                rows.append(json.loads(result.stdout.strip().splitlines()[-1]))
                with np.load(out) as data:  # closes the file (Windows can't delete it open)
                    logits.setdefault(mode, (data["beat"], data["down"]))
    reference = logits["arena"]
    for mode in MODES:
        same = all(np.array_equal(a, b) for a, b in zip(logits[mode], reference, strict=True))
        mine = [r for r in rows if r["mode"] == mode]
        first = min(r["times"][0] for r in mine)
        second = min(r["times"][1] for r in mine)
        peak, after = min(r["peak_mb"] for r in mine), min(r["after_mb"] for r in mine)
        print(
            f"{mode:9} song 1 {first:5.2f} s  song 2 {second:5.2f} s  peak {peak} MB  "
            f"after {after} MB  identical logits: {same}"
        )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(*sys.argv[2:5])
    else:
        main()
