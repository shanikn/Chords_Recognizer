"""Export madmom's chord-feature CNN and key CNN to ONNX, for the Android app.

    uv run --with onnx --with onnxscript python android/tools/export_models.py

The networks are rebuilt in PyTorch from madmom's own weights (the pickled models in
madmom/models) and exported with a dynamic time axis:

    chord_features.onnx  spectrogram (1, T, 113) -> features (1, T, 128)
                         = madmom's _cnncfp_pad, the CNN, _cnncfp_superframes, _cnncfp_avg
    key.onnx             spectrogram (1, T, 105) -> key probabilities (1, 24)
                         = the CNN (an ensemble of one), add_axis, softmax

The spectrograms (framing, STFT, log-filtered spectrogram) and the CRF stay outside the
models; the app reimplements them (see android/docs/PHASE0.md). madmom convolves
(flipped kernel) where PyTorch correlates, so kernels are flipped here. Everything is
float32, as in madmom.

Verification (verify_models.py) compares these models with madmom on real songs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch
from torch import nn

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "app" / "src" / "main" / "assets" / "analysis"
CHORD_BANDS, KEY_BANDS = 113, 105


def _conv(layer) -> nn.Conv2d:
    weights = np.asarray(layer.weights, dtype=np.float32)  # (in, out, time, freq)
    channels_in, channels_out, kt, kf = weights.shape
    conv = nn.Conv2d(channels_in, channels_out, (kt, kf))
    flipped = weights[:, :, ::-1, ::-1].transpose(1, 0, 2, 3)  # madmom convolves
    conv.weight.data = torch.from_numpy(flipped.copy())
    bias = np.broadcast_to(np.asarray(layer.bias, dtype=np.float32), (channels_out,))
    conv.bias.data = torch.from_numpy(np.ascontiguousarray(bias))
    return conv


class _BatchNorm(nn.Module):
    """madmom's BatchNormLayer: (x - mean) * gamma * inv_std + beta, per channel."""

    def __init__(self, layer) -> None:
        super().__init__()
        scale = np.asarray(layer.gamma, np.float32) * np.asarray(layer.inv_std, np.float32)
        shift = np.asarray(layer.beta, np.float32) - np.asarray(layer.mean, np.float32) * scale
        self.register_buffer("scale", torch.from_numpy(scale).view(1, -1, 1, 1))
        self.register_buffer("shift", torch.from_numpy(shift).view(1, -1, 1, 1))

    def forward(self, x):
        return x * self.scale + self.shift


def _activation(fn) -> nn.Module:
    name = getattr(fn, "__name__", "linear")
    return {"relu": nn.ReLU(), "elu": nn.ELU(), "linear": nn.Identity()}[name]


def _layers(network) -> list[nn.Module]:
    """madmom layers (time, freq, channel data) as PyTorch modules (N, C, time, freq)."""
    from madmom.ml.nn import layers as L

    modules = []
    for layer in network.layers:
        if type(layer) is L.ConvolutionalLayer:
            assert layer.pad == "valid" and layer.stride in (None, 1, (1, 1))
            modules += [_conv(layer), _activation(layer.activation_fn)]
        elif type(layer) is L.BatchNormLayer:
            modules += [_BatchNorm(layer), _activation(layer.activation_fn)]
        elif type(layer) is L.MaxPoolLayer:
            # maximum_filter + slicing from size // 2 == floor-mode max pooling
            modules.append(nn.MaxPool2d(tuple(layer.size), tuple(layer.stride)))
        elif type(layer) is L.PadLayer:
            assert tuple(layer.axes) == (0, 1) and layer.value == 0
            w = int(layer.width)
            modules.append(nn.ZeroPad2d((w, w, w, w)))  # (freq left, right, time top, bottom)
        elif type(layer) is L.AverageLayer:
            assert tuple(layer.axis) == (0, 1)
            modules.append(nn.AdaptiveAvgPool2d(1))
        else:
            raise NotImplementedError(type(layer).__name__)
    return modules


class ChordFeatures(nn.Module):
    """(1, T, 113) log spectrogram -> (1, T, 128) features, like CNNChordFeatureProcessor
    after its spectrogram: pad 11 zero frames each side, CNN, 3-frame superframes, mean."""

    def __init__(self, network) -> None:
        super().__init__()
        self.body = nn.Sequential(*_layers(network))

    def forward(self, spectrogram):
        x = nn.functional.pad(spectrogram, (0, 0, 11, 11))[:, None]  # (1, 1, T + 22, 113)
        x = self.body(x)  # (1, 128, T + 2, 13)
        # 113 bins -> 13 after the network, always (fixed by the kernels and pooling)
        x = nn.functional.avg_pool2d(x, (3, 13), stride=1)  # (1, 128, T, 1)
        return x[..., 0].transpose(1, 2)  # (1, T, 128)


class Key(nn.Module):
    """(1, T, 105) log spectrogram -> (1, 24) key probabilities, like
    CNNKeyRecognitionProcessor after its spectrogram."""

    def __init__(self, network) -> None:
        super().__init__()
        self.body = nn.Sequential(*_layers(network))

    def forward(self, spectrogram):
        x = self.body(spectrogram[:, None])  # (1, 24, 1, 1)
        return torch.softmax(x.flatten(1), dim=1)


def networks():
    from madmom.features.chords import CNNChordFeatureProcessor
    from madmom.features.key import CNNKeyRecognitionProcessor

    chord = CNNChordFeatureProcessor().processors[5]
    ensemble = CNNKeyRecognitionProcessor().processors[4]
    members = ensemble.processors[0].processors
    assert len(members) == 1, "the key model is an ensemble of one network"
    return chord, members[0]


def export(module: nn.Module, bands: int, path: Path, output: str) -> None:
    module.eval()
    example = torch.zeros(1, 400, bands)
    torch.onnx.export(
        module,
        (example,),
        str(path),
        input_names=["spectrogram"],
        output_names=[output],
        dynamic_axes={
            "spectrogram": {1: "time"},
            output: {1: "time"} if output == "features" else {},
        },
        opset_version=17,
        dynamo=False,
    )
    print(f"{path.name}: {path.stat().st_size / 2**20:.1f} MB")


def main() -> int:
    OUT.mkdir(exist_ok=True)
    chord, key = networks()
    with torch.no_grad():
        export(ChordFeatures(chord), CHORD_BANDS, OUT / "chord_features.onnx", "features")
        export(Key(key), KEY_BANDS, OUT / "key.onnx", "key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
