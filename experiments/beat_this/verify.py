"""Check the numpy/onnxruntime Beat This! against the original implementation's outputs
saved by export.py --refs: the spectrogram, and beats/downbeats with both the minimal
and the DBN postprocessing.

    uv run python experiments/beat_this/verify.py

Result (2026-09-27, 3 songs): spectrogram within 2.4e-5 relative; minimal beats and
downbeats identical to the original.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import beat_this_onnx as bt  # noqa: E402
from common import REFERENCE_SONGS, out_dir  # noqa: E402


def same(a, b) -> bool:
    return len(a) == len(b) and bool(np.allclose(a, b))


def main():
    ref_dir = out_dir("beat_this")
    session = bt.session(ref_dir / "beat_this_small0.onnx")
    for key in REFERENCE_SONGS:
        spect_ref = np.load(ref_dir / f"{key}.spect.npy")
        spect = bt.log_mel(np.load(ref_dir / f"{key}.signal22k.npy"))
        rel = np.abs(spect - spect_ref).max() / np.abs(spect_ref).max()
        logits = bt.frames_logits(spect, session)
        beats, downs = bt.postprocess(*logits)
        dbn = bt.postprocess_dbn(*logits)
        dbn_beats, dbn_downs = dbn[:, 0], dbn[dbn[:, 1] == 1][:, 0]
        print(
            f"{key}: spectrogram rel diff {rel:.1e}; minimal: beats "
            f"{same(beats, np.load(ref_dir / f'{key}.beats.npy'))}, downbeats "
            f"{same(downs, np.load(ref_dir / f'{key}.downbeats.npy'))}; dbn: beats "
            f"{same(dbn_beats, np.load(ref_dir / f'{key}.dbn-beats.npy'))}, downbeats "
            f"{same(dbn_downs, np.load(ref_dir / f'{key}.dbn-downbeats.npy'))}"
        )


if __name__ == "__main__":
    main()
