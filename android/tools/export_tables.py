"""Export the analysis's constant tables for the C++ core, as .npy files.

    uv run python android/tools/export_tables.py

Everything here is fixed by the sample rate, frame sizes and the app's settings, so it is
computed once by the Python pipeline's own code and loaded by the core; the core never
rebuilds a filterbank or an HMM, and they are identical by construction:

    madmom spectrograms   chord_window.npy (8192, float64: np.hanning / 32767, as madmom
                          scales windows for int16 audio), chord_filterbank.npy (4096 x 113),
                          key_filterbank.npy (4096 x 105), float32
    Beat This! frontend   bt_window.npy (1024), bt_mel_filterbank.npy (513 x 128), float32
    DBN, per meter m      dbn{m}_states / _pointers (uint32): each state's predecessors;
                          dbn{m}_log_probs (float64): their transition log-probabilities;
                          dbn{m}_log_initial (float64); dbn{m}_om_pointers (uint32);
                          dbn{m}_positions (float64): bar position of each state
    CRF (chords)          crf_pi, crf_A, crf_W, crf_c, crf_tau (float32)
    settings.json         the numbers the core needs (fps, frame sizes, thresholds, ...)

Written to android/app/src/main/assets/analysis/, next to the ONNX models. The DBN and
CRF tables come from madmom's model files: CC BY-NC-SA 4.0 (see docs/PHASE0.md).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ANDROID = HERE.parent
OUT = ANDROID / "app" / "src" / "main" / "assets" / "analysis"


def save(name: str, array, dtype) -> None:
    array = np.ascontiguousarray(np.asarray(array), dtype=dtype)
    np.save(OUT / f"{name}.npy", array)


def spectrogram_tables() -> dict:
    from madmom.features.chords import CNNChordFeatureProcessor
    from madmom.features.key import CNNKeyRecognitionProcessor

    settings = {}
    for name, processor in (
        ("chord", CNNChordFeatureProcessor()),
        ("key", CNNKeyRecognitionProcessor()),
    ):
        steps = processor.processors
        x = np.zeros(44100 * 2, dtype=np.int16)
        for step in steps[:4]:
            x = step(x)
        frames = x.stft.frames
        assert x.stft.dtype == np.complex64 and x.dtype == np.float32
        if name == "chord":
            save("chord_window", x.stft.fft_window, np.float64)
        else:  # same frame size, same window
            assert np.array_equal(x.stft.fft_window, np.load(OUT / "chord_window.npy"))
        save(f"{name}_filterbank", x.filterbank, np.float32)
        spec = steps[3]
        settings[name] = {
            "sample_rate": 44100,
            "frame_size": int(frames.frame_size),
            "hop_size": float(frames.hop_size),
            "fps": float(frames.signal.sample_rate / frames.hop_size),
            "bands": int(x.filterbank.shape[1]),
            "log_mul": float(spec.mul),
            "log_add": float(spec.add),
        }
    return settings


def beat_this_tables() -> dict:
    from chordchart import beat_this as bt

    save("bt_window", bt._WINDOW, np.float32)
    save("bt_mel_filterbank", bt._FILTERBANK, np.float32)
    return {
        "sample_rate": bt.SR,
        "n_fft": bt.N_FFT,
        "hop": bt.HOP,
        "n_mels": bt.N_MELS,
        "fps": bt.FPS,
        "chunk": bt.CHUNK,
        "border": bt.BORDER,
    }


def dbn_tables() -> dict:
    from chordchart import beat_this as bt

    dbn = bt.make_dbn((3, 4))
    meters = []
    for beats_per_bar, hmm in zip(dbn.beats_per_bar, dbn.hmms, strict=True):
        tm, om = hmm.transition_model, hmm.observation_model
        m = int(beats_per_bar)
        save(f"dbn{m}_states", tm.states, np.uint32)
        save(f"dbn{m}_pointers", tm.pointers, np.uint32)
        save(f"dbn{m}_log_probs", tm.log_probabilities, np.float64)
        save(f"dbn{m}_log_initial", np.log(hmm.initial_distribution), np.float64)
        save(f"dbn{m}_om_pointers", om.pointers, np.uint32)
        save(f"dbn{m}_positions", tm.state_space.state_positions, np.float64)
        meters.append(m)
    return {
        "meters": meters,
        "fps": float(dbn.fps),
        "threshold": float(dbn.threshold),
        "correct": bool(dbn.correct),
        "observation_lambda": float(dbn.hmms[0].observation_model.observation_lambda),
    }


def crf_tables() -> dict:
    from madmom.features.chords import CRFChordRecognitionProcessor

    crf = CRFChordRecognitionProcessor().processors[0]
    for name in ("pi", "A", "W", "c", "tau"):
        save(f"crf_{name}", getattr(crf, name), np.float32)
    return {"fps": 10.0}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = {
        "spectrogram": spectrogram_tables(),
        "beat_this": beat_this_tables(),
        "dbn": dbn_tables(),
        "crf": crf_tables(),
    }
    (OUT / "settings.json").write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    # chord_features.onnx and key.onnx live here too (export_models.py). The Beat This!
    # model stays in chordchart/models; it is copied here for the app and the core's tests
    # (git-ignored here, and the Android build copies it too).
    from chordchart import beat_this as bt

    shutil.copy2(bt.MODEL, OUT / bt.MODEL.name)
    total = sum(p.stat().st_size for p in OUT.iterdir())
    print(f"{len(list(OUT.iterdir()))} files, {total / 2**20:.1f} MB in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
