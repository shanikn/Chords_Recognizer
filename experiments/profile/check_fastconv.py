"""Do madmom's CNNs give the same chords and key with chordchart.fastconv as without?
Runs both on every cached download and reports any difference.

    uv run python experiments/profile/check_fastconv.py [LIMIT]
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

from chordchart.fastconv import accelerated  # noqa: E402
from chordchart.fetch import decode_section, load_signal  # noqa: E402


def main():
    from madmom.features.chords import CNNChordFeatureProcessor, CRFChordRecognitionProcessor
    from madmom.features.key import CNNKeyRecognitionProcessor, key_prediction_to_label

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    crf = CRFChordRecognitionProcessor()
    models = {
        "madmom": (CNNChordFeatureProcessor(), CNNKeyRecognitionProcessor()),
        "fast": (
            accelerated(CNNChordFeatureProcessor()),
            accelerated(CNNKeyRecognitionProcessor()),
        ),
    }
    songs = sorted(
        p for p in DOWNLOADS.iterdir() if p.suffix in {".webm", ".m4a", ".mp3", ".opus"}
    )[:limit]
    differing = 0
    totals = {name: 0.0 for name in models}
    for song in songs:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            decode_section(song, wav)
            audio = load_signal(wav)
        out = {}
        for name, (cnn, key) in models.items():
            began = time.perf_counter()
            rows = crf(cnn(audio))
            label = key_prediction_to_label(key(audio))
            totals[name] += time.perf_counter() - began
            out[name] = ([(round(s, 2), round(e, 2), lab) for s, e, lab in rows], label)
        same = out["madmom"] == out["fast"]
        differing += not same
        print(f"{'same' if same else 'DIFFERENT':9} {song.name}", flush=True)
        if not same:
            a, b = out["madmom"], out["fast"]
            print("   key", a[1], b[1], "| segments", len(a[0]), len(b[0]))
            print(
                "   first diffs", [(x, y) for x, y in zip(a[0], b[0], strict=False) if x != y][:5]
            )
    print(f"\n{len(songs)} songs, {differing} different; chords+key time madmom "
          f"{totals['madmom']:.0f} s, fast {totals['fast']:.0f} s")  # fmt: skip


if __name__ == "__main__":
    main()
