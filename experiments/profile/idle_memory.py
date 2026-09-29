"""What stays in memory after an analysis? Per process (the app and its DBN pool
workers): working set (RSS) and private bytes, after loading the models, after a
first analysis, after a cache-hit analysis, and after gc; with variations:

    --no-arena     onnxruntime without its CPU memory arena (Beat This!)
    --dbn-threads N

    uv run --with psutil python experiments/profile/idle_memory.py [--no-arena] [--dbn-threads 1]
"""

import gc
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

SONG = DOWNLOADS / "youtube-20iOlPwz0J0.webm"  # longest cached song


def snapshot(label: str, peak_mb: int | None = None) -> None:
    import psutil

    me = psutil.Process()
    rows = [("app", me)] + [(f"worker {c.pid}", c) for c in me.children(recursive=True)]
    parts = []
    total_rss = total_private = 0
    for name, proc in rows:
        try:
            info = proc.memory_full_info() if sys.platform != "win32" else proc.memory_info()
        except psutil.Error:
            continue
        private = getattr(info, "private", getattr(info, "uss", 0))
        total_rss += info.rss
        total_private += private
        parts.append(f"{name}: rss {info.rss >> 20} / private {private >> 20}")
    peak = f"  peak during: {peak_mb} MB" if peak_mb is not None else ""
    print(
        f"{label:30} tree rss {total_rss >> 20:5} MB, private {total_private >> 20:5} MB"
        f"{peak}  [{'; '.join(parts)}]",
        flush=True,
    )


def measured(fn):
    import psutil

    me = psutil.Process()
    peak = [0]
    done = threading.Event()

    def sample():
        while not done.is_set():
            total = me.memory_info().rss
            for c in me.children(recursive=True):
                try:
                    total += c.memory_info().rss
                except psutil.Error:
                    pass
            peak[0] = max(peak[0], total)
            time.sleep(0.01)

    threading.Thread(target=sample, daemon=True).start()
    began = time.perf_counter()
    result = fn()
    done.set()
    return result, time.perf_counter() - began, peak[0] >> 20


def main() -> None:
    args = sys.argv[1:]
    if "--no-arena" in args:
        import chordchart.beat_this as bt

        original = bt.BeatThisModel.__init__

        def init(self, threads=1, path=bt.MODEL):
            import onnxruntime as ort

            real = ort.SessionOptions

            class Options(real):
                def __init__(self):
                    super().__init__()
                    self.enable_cpu_mem_arena = False

            ort.SessionOptions = Options
            try:
                original(self, threads, path)
            finally:
                ort.SessionOptions = real

        bt.BeatThisModel.__init__ = init
    if "--dbn-threads" in args:
        import chordchart.beat_this as bt

        threads = int(args[args.index("--dbn-threads") + 1])
        make = bt.make_dbn
        bt.make_dbn = lambda beats_per_bar, threads_=1, **kw: make(beats_per_bar, threads)

    from chordchart.pipeline import DEFAULT_THREADS, analyze
    from chordchart.processors import Processors

    snapshot("start")
    procs = Processors(num_threads=DEFAULT_THREADS)
    snapshot("models loaded")
    _, took, peak = measured(lambda: analyze(SONG, processors=procs, refresh=True))
    snapshot(f"after analysis ({took:.1f} s)", peak)
    gc.collect()
    snapshot("after gc")
    _, took, peak = measured(lambda: analyze(SONG, processors=procs))
    snapshot(f"after cache hit ({took:.1f} s)", peak)
    _, took, peak = measured(lambda: analyze(SONG, processors=procs, refresh=True))
    snapshot(f"after 2nd analysis ({took:.1f} s)", peak)
    procs.close()


if __name__ == "__main__":
    main()
