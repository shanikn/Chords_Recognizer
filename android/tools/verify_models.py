"""Do the exported ONNX models give the Python pipeline's results?

    uv run python android/tools/verify_models.py [--madmom N]

On every cached song (the desktop app's download cache): the madmom spectrograms go
through (a) the desktop pipeline's networks (madmom + chordchart.fastconv) and (b) the
ONNX models on onnxruntime. Reported: the largest feature / probability difference, and
whether the decoded chords (madmom's CRF) and the key label are identical. The first N
songs (default 3) are also checked against madmom's own, unaccelerated networks.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "experiments"))
from common import DOWNLOADS  # noqa: E402

MODELS = HERE.parent / "models"


def main() -> int:
    import onnxruntime as ort
    from madmom.features.chords import CNNChordFeatureProcessor, CRFChordRecognitionProcessor
    from madmom.features.key import CNNKeyRecognitionProcessor, key_prediction_to_label

    from chordchart.fastconv import accelerated
    from chordchart.fetch import decode_section, load_signal

    madmom_n = int(sys.argv[sys.argv.index("--madmom") + 1]) if "--madmom" in sys.argv else 3
    chord_ref, key_ref = CNNChordFeatureProcessor(), CNNKeyRecognitionProcessor()
    chord_fast, key_fast = (
        accelerated(CNNChordFeatureProcessor()),
        accelerated(CNNKeyRecognitionProcessor()),
    )
    crf = CRFChordRecognitionProcessor()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    chord_onnx = ort.InferenceSession(str(MODELS / "chord_features.onnx"), options)
    key_onnx = ort.InferenceSession(str(MODELS / "key.onnx"), options)

    songs = sorted(p for p in DOWNLOADS.iterdir() if p.suffix in {".webm", ".m4a", ".mp3", ".opus"})
    rows = []
    for i, song in enumerate(songs):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            decode_section(song, wav)
            audio = load_signal(wav)
        chord_spec = audio
        for step in chord_fast.processors[:4]:
            chord_spec = step(chord_spec)
        key_spec = audio
        for step in key_fast.processors[:4]:
            key_spec = step(key_spec)
        chord_spec = np.asarray(chord_spec, dtype=np.float32)
        key_spec = np.asarray(key_spec, dtype=np.float32)

        began = time.perf_counter()
        feats_onnx = chord_onnx.run(None, {"spectrogram": chord_spec[None]})[0][0]
        key_onnx_p = key_onnx.run(None, {"spectrogram": key_spec[None]})[0]
        onnx_s = time.perf_counter() - began
        feats_fast = _net_tail(chord_fast, chord_spec, start=4)
        key_fast_p = _net_tail(key_fast, key_spec, start=4)
        rows.append(
            _compare(
                song.name,
                "fastconv",
                feats_fast,
                feats_onnx,
                key_fast_p,
                key_onnx_p,
                crf,
                key_prediction_to_label,
                onnx_s,
            )
        )
        if i < madmom_n:
            feats_ref = _net_tail(chord_ref, chord_spec, start=4)
            key_ref_p = _net_tail(key_ref, key_spec, start=4)
            rows.append(
                _compare(
                    song.name,
                    "madmom",
                    feats_ref,
                    feats_onnx,
                    key_ref_p,
                    key_onnx_p,
                    crf,
                    key_prediction_to_label,
                    onnx_s,
                )
            )
    same = [r for r in rows if r["chords"] and r["key"]]
    print(f"\n{len(same)}/{len(rows)} comparisons with identical chords and key")
    worst = max(rows, key=lambda r: r["feat_rel"])
    print(
        f"largest feature difference: {worst['feat_abs']:.2e} abs, "
        f"{worst['feat_rel']:.2e} relative ({worst['song']}, vs {worst['ref']})"
    )
    print(f"largest key probability difference: {max(r['key_abs'] for r in rows):.2e}")
    return 0 if len(same) == len(rows) else 1


def _net_tail(processor, data, start):
    """Run the processor's steps after the spectrogram (network and what follows)."""
    for step in processor.processors[start:]:
        data = step(data)
    return np.asarray(data, dtype=np.float32)


def _compare(name, ref, feats_ref, feats_onnx, key_ref, key_onnx, crf, to_label, onnx_s):
    chords_ref = [
        (round(s, 2), round(e, 2), lab) for s, e, lab in crf(feats_ref.astype(np.float64))
    ]
    chords_onnx = [
        (round(s, 2), round(e, 2), lab) for s, e, lab in crf(feats_onnx.astype(np.float64))
    ]
    row = {
        "song": name,
        "ref": ref,
        "feat_abs": float(np.abs(feats_ref - feats_onnx).max()),
        "feat_rel": float(np.abs(feats_ref - feats_onnx).max() / (np.abs(feats_ref).max() + 1e-12)),
        "key_abs": float(np.abs(key_ref - key_onnx).max()),
        "chords": chords_ref == chords_onnx,
        "key": to_label(key_ref) == to_label(key_onnx),
    }
    chords = "same" if row["chords"] else "DIFFERENT"
    key = "same" if row["key"] else "DIFFERENT"
    print(
        f"{name:28} vs {ref:8} features {row['feat_rel']:.1e} rel, "
        f"key probs {row['key_abs']:.1e}; chords {chords}, key {key} "
        f"({to_label(key_onnx)}); onnx {onnx_s:.1f} s",
        flush=True,
    )
    return row


if __name__ == "__main__":
    sys.exit(main())
