"""Beat tracking split into its parts: resample + log-mel, the Beat This! network, and
madmom's DBN (3/4 and 4/4), one song, models loaded first.

    uv run python experiments/profile/profile_beats.py [SONG_FILE] [THREADS]
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

from chordchart import beat_this  # noqa: E402
from chordchart.beats import _samples  # noqa: E402
from chordchart.fetch import decode_section, load_signal  # noqa: E402


def main():
    import soxr

    song = Path(sys.argv[1]) if len(sys.argv) > 1 else DOWNLOADS / "youtube-hLQl3WQQoQ0.webm"
    threads = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(song, wav)
        audio = load_signal(wav)
    model = beat_this.BeatThisModel(threads=threads)
    dbn = beat_this.make_dbn((3, 4), threads=threads)
    samples = _samples(audio)

    t = time.perf_counter()
    spect = beat_this.log_mel(soxr.resample(samples, in_rate=44100, out_rate=beat_this.SR))
    print(f"resample + log-mel   {time.perf_counter() - t:6.2f} s  ({spect.shape})")
    t = time.perf_counter()
    logits = model.logits(samples)
    print(f"logits (incl. mel)   {time.perf_counter() - t:6.2f} s")
    activations = beat_this.dbn_activations(*logits)
    t = time.perf_counter()
    dbn(activations)
    print(f"DBN ({threads} threads)       {time.perf_counter() - t:6.2f} s")
    single = beat_this.make_dbn((3, 4), threads=1)
    t = time.perf_counter()
    single(activations)
    print(f"DBN (1 thread)       {time.perf_counter() - t:6.2f} s")
    for pool in _pools(dbn) + _pools(single):
        pool.terminate()


def _pools(dbn):
    from chordchart.processors import _find_pools

    return list(_find_pools([dbn]))


if __name__ == "__main__":
    main()
