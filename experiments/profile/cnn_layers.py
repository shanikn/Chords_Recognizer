"""The chord and key CNNs layer by layer: type, shapes, time. madmom's layers are compiled
(Cython), so cProfile can't see inside them; this times each layer on real input.

    uv run python experiments/profile/cnn_layers.py
"""

import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

from chordchart.fetch import decode_section, load_signal  # noqa: E402
from chordchart.processors import Processors  # noqa: E402


def networks(procs):
    """(name, preprocessing, first network) for the chord features CNN and the key CNN."""
    from madmom.ml.nn import NeuralNetwork, NeuralNetworkEnsemble

    found = []
    for name, proc in (("chords", procs.chord_features), ("key", procs.key)):
        steps = list(proc.processors)
        i = next(i for i, p in enumerate(steps) if isinstance(p, NeuralNetwork | NeuralNetworkEnsemble))
        net = steps[i]
        if isinstance(net, NeuralNetworkEnsemble):
            net = net.processors[0].processors[0]
        found.append((name, steps[:i], net))
    return found


def main():
    import cv2

    print("cv2 threads:", cv2.getNumThreads(), "| numpy BLAS:", np.show_config.__module__)
    with Processors(num_threads=1) as procs, tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(DOWNLOADS / "youtube-hLQl3WQQoQ0.webm", wav)
        audio = load_signal(wav)
        for name, pre, net in networks(procs):
            data = audio
            for p in pre:
                data = p(data)
            print(f"\n== {name}: input {np.asarray(data).shape} {np.asarray(data).dtype}")
            x = np.asarray(data)
            if True:
                for layer in net.layers:
                    began = time.perf_counter()
                    y = layer(x)
                    took = time.perf_counter() - began
                    w = getattr(layer, "weights", None)
                    wshape = None if w is None else np.asarray(w).shape
                    print(f"   {type(layer).__name__:22} {str(x.shape):>22} -> {str(y.shape):22} "
                          f"weights {str(wshape):18} {took:6.2f} s")  # fmt: skip
                    x = y


if __name__ == "__main__":
    main()
