"""Download step for the Beat This! experiments: fetch the small0 checkpoint, export it to
ONNX, and save reference outputs of the original implementation. Nothing it writes is
committed (experiments/out/beat_this/).

Runs in a throwaway environment, so the project never depends on beat-this or torchaudio:

    uv run --no-project --python 3.12 --with beat-this --with onnx --with onnxruntime \\
        --with "torch==2.14.0" python experiments/beat_this/export.py [--refs]

Writes beat_this_small0.onnx and checks it against torch at several lengths. With --refs,
also for three cached songs: <key>.signal22k.npy (the resampled mono signal),
<key>.spect.npy (torchaudio log-mel), and the original Audio2Beats' beats/downbeats with
its minimal postprocessing (<key>.beats.npy, .downbeats.npy) and its DBN
(<key>.dbn-beats.npy, .dbn-downbeats.npy), for verify.py.

Beat This! (CPJKU, https://github.com/CPJKU/beat_this): code and weights MIT.
"""

import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soxr
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from beat_this.inference import Audio2Beats, load_model  # noqa: E402
from beat_this.preprocessing import LogMelSpect  # noqa: E402
from common import DOWNLOADS, REFERENCE_SONGS, out_dir  # noqa: E402


class Logits(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, spect):
        out = self.model(spect)
        return out["beat"], out["downbeat"]


def decode_44k_mono(path: Path) -> np.ndarray:
    """Same decode as chordchart.fetch: ffmpeg, mono, 44.1 kHz, 16-bit."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "44100", "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def main():
    out = out_dir("beat_this")
    model = load_model("small0", "cpu").eval()
    # eval() on the wrapper too: export restores the wrapper's mode afterwards, and a
    # wrapper left in training mode switches the whole model to training (dropout,
    # batch statistics), which makes every later torch reference wrong.
    wrapped = Logits(model).eval()
    onnx_path = out / "beat_this_small0.onnx"
    torch.onnx.export(
        wrapped, (torch.randn(1, 1500, 128),), str(onnx_path), input_names=["spect"],
        output_names=["beat", "downbeat"], opset_version=17, dynamo=False,
        dynamic_axes={"spect": {1: "time"}, "beat": {1: "time"}, "downbeat": {1: "time"}},
    )  # fmt: skip
    print("exported", onnx_path, onnx_path.stat().st_size // 1024, "KB")

    import onnxruntime as ort

    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    for frames in (1500, 777, 60):
        x = torch.rand(1, frames, 128) * 6
        with torch.inference_mode():
            ref = [t.numpy() for t in wrapped(x)]
        got = session.run(None, {"spect": x.numpy()})
        worst = max(np.abs(g - r).max() for g, r in zip(got, ref, strict=True))
        print(f"onnx vs torch, {frames} frames: max |diff| {worst:.2e}")

    if "--refs" not in sys.argv:
        return
    spect = LogMelSpect()
    minimal = Audio2Beats(checkpoint_path="small0", device="cpu", dbn=False)
    with_dbn = Audio2Beats(checkpoint_path="small0", device="cpu", dbn=True)
    for key in REFERENCE_SONGS:
        signal44 = decode_44k_mono(DOWNLOADS / f"{key}.webm")
        signal22 = soxr.resample(signal44, in_rate=44100, out_rate=22050)
        np.save(out / f"{key}.signal22k.npy", signal22.astype(np.float32))
        with torch.inference_mode():
            np.save(
                out / f"{key}.spect.npy", spect(torch.tensor(signal22, dtype=torch.float32)).numpy()
            )
        for label, tracker in (("", minimal), ("dbn-", with_dbn)):
            began = time.perf_counter()
            beats, downbeats = tracker(signal44, 44100)
            seconds = time.perf_counter() - began
            print(
                f"{key}: original {label or 'minimal-'}postprocessing {seconds:.1f} s, "
                f"{len(beats)} beats, {len(downbeats)} downbeats"
            )
            np.save(out / f"{key}.{label}beats.npy", np.asarray(beats))
            np.save(out / f"{key}.{label}downbeats.npy", np.asarray(downbeats))


if __name__ == "__main__":
    main()
