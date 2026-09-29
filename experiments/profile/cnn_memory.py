"""Peak memory (RSS) of the chord + key CNNs on one song, madmom's convolution vs
chordchart.fastconv, each in a fresh process.

    uv run --with psutil python experiments/profile/cnn_memory.py [SONG]
"""

import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402


def worker(mode: str, song: str) -> None:
    import psutil

    from chordchart.fastconv import accelerated
    from chordchart.fetch import decode_section, load_signal

    from madmom.features.chords import CNNChordFeatureProcessor, CRFChordRecognitionProcessor
    from madmom.features.key import CNNKeyRecognitionProcessor

    import chordchart.fastconv as fastconv

    if mode.startswith("fast") and mode != "fast":
        fastconv._BLOCK_BYTES = int(mode[4:]) * 2**20  # e.g. fast16: 16 MB blocks
    wrap = accelerated if mode.startswith("fast") else (lambda p: p)
    cnn, crf, key = wrap(CNNChordFeatureProcessor()), CRFChordRecognitionProcessor(), wrap(CNNKeyRecognitionProcessor())
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(Path(song), wav)
        audio = load_signal(wav)
    me = psutil.Process()
    base = me.memory_info().rss
    peak = [base]
    done = threading.Event()

    def sample():
        while not done.is_set():
            peak[0] = max(peak[0], me.memory_info().rss)
            time.sleep(0.01)

    threading.Thread(target=sample, daemon=True).start()
    t = time.perf_counter()
    crf(cnn(audio))
    key(audio)
    took = time.perf_counter() - t
    done.set()
    after = me.memory_info().rss
    mb = 2**20
    print(f"{mode:7} {took:5.1f} s  before {base // mb} MB  peak {peak[0] // mb} MB  "
          f"(+{(peak[0] - base) // mb})  after {after // mb} MB")  # fmt: skip


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        worker(sys.argv[2], sys.argv[3])
    else:
        song = sys.argv[1] if len(sys.argv) > 1 else str(DOWNLOADS / "youtube-20iOlPwz0J0.webm")
        for mode in sys.argv[2:] or ("madmom", "fast"):
            subprocess.run([sys.executable, __file__, "--worker", mode, song], check=True)
