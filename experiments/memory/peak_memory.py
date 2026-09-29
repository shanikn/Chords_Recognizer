"""Peak memory of beat tracking, Beat This! + DBN vs madmom, on one song (default: the
longest cached one). Each tracker runs in its own fresh process (models loaded, then
the song tracked), and the whole process tree is sampled every 20 ms: madmom does much
of its work in worker processes, so the parent alone would understate it.

    uv run --with psutil python experiments/memory/peak_memory.py [SONG_FILE]

Reported: peak of the tree's summed working set (RSS), overall and while tracking (after
the models are loaded). (The venv's python.exe on Windows is a launcher that starts the
real interpreter as a child, so only the whole tree is meaningful.)
"""

import subprocess
import sys
import time
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import cached_songs  # noqa: E402

CHILD = r"""
import sys, time, tempfile
from pathlib import Path
from chordchart.beats import track_beats, track_beats_madmom
from chordchart.fetch import decode_section, load_signal
from chordchart.pipeline import DEFAULT_THREADS
from chordchart.processors import Processors
tracker = {"beat_this": track_beats, "madmom": track_beats_madmom}[sys.argv[1]]
with Processors(num_threads=DEFAULT_THREADS) as procs:
    if sys.argv[1] == "madmom":
        procs.downbeat_rnn  # load madmom's tracker up front, like the old default
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(Path(sys.argv[2]), wav)
        audio = load_signal(wav)
        print("READY", flush=True)
        began = time.perf_counter()
        beats = tracker(audio, (3, 4), procs)
        took = time.perf_counter() - began
        print(f"DONE {took:.1f} {len(beats.times)} {beats.meter}", flush=True)
"""


def tree_rss(proc: psutil.Process) -> tuple[int, int]:
    try:
        own = proc.memory_info().rss
        children = sum(c.memory_info().rss for c in proc.children(recursive=True) if c.is_running())
        return own + children, own
    except psutil.Error:
        return 0, 0


def measure(tracker: str, song: Path) -> dict:
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, tracker, str(song)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
    )  # fmt: skip
    ps = psutil.Process(proc.pid)
    peak_tree = peak_parent = peak_tracking = 0
    tracking = False
    lines = []
    import threading

    def read():
        for line in proc.stdout:
            lines.append(line.strip())

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    while proc.poll() is None:
        tree, own = tree_rss(ps)
        peak_tree, peak_parent = max(peak_tree, tree), max(peak_parent, own)
        tracking = tracking or "READY" in lines
        if tracking:
            peak_tracking = max(peak_tracking, tree)
        time.sleep(0.02)
    reader.join(timeout=5)
    done = next(line for line in lines if line.startswith("DONE")).split()
    return {
        "tracker": tracker, "seconds": float(done[1]), "beats": int(done[2]), "meter": int(done[3]),
        "peak_tree_mb": peak_tree / 2**20, "peak_parent_mb": peak_parent / 2**20,
        "peak_tracking_mb": peak_tracking / 2**20,
    }  # fmt: skip


def _duration(path: Path) -> float:
    import json

    meta = path.with_suffix(".json")
    return json.loads(meta.read_text(encoding="utf-8")).get("duration") or 0 if meta.exists() else 0


def main():
    song = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if song is None:  # the longest cached song, by the download cache's metadata
        song = max(cached_songs(), key=_duration)
    print(f"song: {song.name}")
    for tracker in ("beat_this", "madmom"):
        r = measure(tracker, song)
        print(
            f"{tracker:10} tracking {r['seconds']:5.1f} s, {r['beats']} beats, meter {r['meter']}; "
            f"peak memory: whole process tree {r['peak_tree_mb']:6.0f} MB "
            f"(while tracking {r['peak_tracking_mb']:6.0f} MB)"
        )


if __name__ == "__main__":
    main()
