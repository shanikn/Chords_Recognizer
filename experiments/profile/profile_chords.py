"""Where does chord recognition (and key detection) spend its time? cProfile on one song,
models loaded first, each stage alone.

    uv run python experiments/profile/profile_chords.py [SONG_FILE]
"""

import cProfile
import pstats
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

from chordchart.fetch import decode_section, load_signal  # noqa: E402
from chordchart.key import detect_key  # noqa: E402
from chordchart.processors import Processors  # noqa: E402
from chordchart.recognizers.madmom_crf import MadmomCRFRecognizer  # noqa: E402


def main():
    song = Path(sys.argv[1]) if len(sys.argv) > 1 else DOWNLOADS / "youtube-hLQl3WQQoQ0.webm"
    with Processors(num_threads=1) as procs, tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(song, wav)
        audio = load_signal(wav)
        recognizer = MadmomCRFRecognizer(procs)
        for name, fn in (("chords", lambda: recognizer.recognize(audio)), ("key", lambda: detect_key(audio, procs))):
            profile = cProfile.Profile()
            began = time.perf_counter()
            profile.enable()
            fn()
            profile.disable()
            print(f"\n=== {name}: {time.perf_counter() - began:.1f} s")
            stats = pstats.Stats(profile).sort_stats("cumulative")
            stats.print_stats(18)


if __name__ == "__main__":
    main()
