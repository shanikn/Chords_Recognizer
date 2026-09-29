"""chordchart/fastconv.py: the faster convolution gives madmom's results."""

import numpy as np
import pytest

from chordchart.fastconv import FastConvolution, accelerated


def _layer(channels, features, size_time, size_freq, seed=0):
    from madmom.ml.nn.activations import relu
    from madmom.ml.nn.layers import ConvolutionalLayer

    rng = np.random.default_rng(seed)
    weights = rng.standard_normal((channels, features, size_time, size_freq)).astype(np.float32)
    bias = rng.standard_normal(features).astype(np.float32)
    return ConvolutionalLayer(weights, bias, activation_fn=relu)


# Odd and even kernels (the chord CNN's last layer is 9 x 12), one and many channels.
@pytest.mark.parametrize("shape", [(1, 4, 3, 3), (5, 6, 3, 3), (3, 4, 9, 12), (4, 2, 1, 1), (2, 3, 5, 4)])
def test_matches_madmom(shape):
    layer = _layer(*shape)
    rng = np.random.default_rng(1)
    data = rng.standard_normal((40, 30, shape[0]) if shape[0] > 1 else (40, 30)).astype(np.float32)
    expected = layer(data)
    got = FastConvolution(layer)(data)
    assert got.shape == expected.shape
    np.testing.assert_allclose(got, expected, rtol=1e-4, atol=1e-4)


def test_long_input_is_processed_in_blocks(monkeypatch):
    import chordchart.fastconv as fastconv

    monkeypatch.setattr(fastconv, "_BLOCK_BYTES", 1000)  # a few frames per block
    layer = _layer(3, 4, 3, 3)
    data = np.random.default_rng(2).standard_normal((50, 20, 3)).astype(np.float32)
    np.testing.assert_allclose(FastConvolution(layer)(data), layer(data), rtol=1e-4, atol=1e-4)


def test_madmom_models_are_accelerated():
    from madmom.features.chords import CNNChordFeatureProcessor
    from madmom.features.key import CNNKeyRecognitionProcessor

    from chordchart.fastconv import _networks

    for processor in (CNNChordFeatureProcessor(), CNNKeyRecognitionProcessor()):
        layers = [layer for net in _networks(accelerated(processor)) for layer in net.layers]
        assert any(isinstance(layer, FastConvolution) for layer in layers)
        assert not any(type(layer).__name__ == "ConvolutionalLayer" for layer in layers)
