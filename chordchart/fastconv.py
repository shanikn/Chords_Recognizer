"""A faster forward pass for madmom's convolutional layers.

madmom convolves every (input channel, filter) pair separately: 64 x 128 = 8192 small 2-D
convolutions in the chord CNN's last layer alone. Profiling (experiments/profile) showed
these loops are ~95% of chord recognition and key detection time. Here each layer is one
matrix product per block of frames (im2col + BLAS), the same arithmetic in float32 —
results agree with madmom's to float rounding (tests/test_fastconv.py).

Only "valid" convolutions with stride 1 are replaced; anything else keeps madmom's code.
"""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

# Bytes of unfolded input per block: bounds the extra memory to ~this much however long
# the song is.
_BLOCK_BYTES = 64 * 2**20


class FastConvolution:
    """Replaces one madmom ConvolutionalLayer; same call interface as madmom layers."""

    def __init__(self, layer) -> None:
        weights = np.asarray(layer.weights, dtype=np.float32)
        channels, features, size_time, size_freq = weights.shape
        # madmom convolves (flipped kernel); a matrix product correlates, so flip here.
        flipped = weights[:, :, ::-1, ::-1]
        # (time, freq, channels) x features, matching the unfolded windows' layout
        self.matrix = np.ascontiguousarray(flipped.transpose(2, 3, 0, 1).reshape(-1, features))
        self.bias = np.asarray(layer.bias, dtype=np.float32)
        self.activation_fn = layer.activation_fn
        self.kernel = (size_time, size_freq)
        self.channels = channels
        self.original = layer

    def __call__(self, data, **kwargs):
        return self.activate(data, **kwargs)

    def activate(self, data, **kwargs):
        if data.ndim == 2:
            data = data[:, :, np.newaxis]
        data = np.ascontiguousarray(data, dtype=np.float32)
        frames, bins, channels = data.shape
        if channels != self.channels:
            raise ValueError("Number of channels in weight vector different from number of channels of input data!")
        size_time, size_freq = self.kernel
        out_frames, out_bins = frames - size_time + 1, bins - size_freq + 1
        out = np.empty((out_frames, out_bins, self.matrix.shape[1]), dtype=np.float32)
        # windows[t, f] is (size_time, size_freq, channels): a view, nothing copied yet.
        # Channels innermost, as in `data`, so unfolding a block copies contiguous runs.
        windows = sliding_window_view(data, (size_time, size_freq), axis=(0, 1)).transpose(0, 1, 3, 4, 2)
        row = out_bins * self.matrix.shape[0] * 4
        step = max(1, _BLOCK_BYTES // row)
        for start in range(0, out_frames, step):
            block = windows[start : start + step].reshape(-1, self.matrix.shape[0])
            np.matmul(block, self.matrix, out=out[start : start + step].reshape(-1, self.matrix.shape[1]))
        out += self.bias
        if self.activation_fn is not None:
            self.activation_fn(out, out=out)
        return out


def _replaceable(layer) -> bool:
    from madmom.ml.nn.layers import ConvolutionalLayer

    return (
        type(layer) is ConvolutionalLayer
        and layer.pad == "valid"
        and layer.stride in (None, 1, (1, 1))
        and np.ndim(layer.weights) == 4
    )


def _networks(processor):
    from madmom.ml.nn import NeuralNetwork

    if isinstance(processor, NeuralNetwork):
        yield processor
        return
    for child in getattr(processor, "processors", None) or ():
        yield from _networks(child)


def accelerated(processor):
    """Swap the convolutional layers of every network inside a madmom processor, in place,
    and return the processor."""
    for network in _networks(processor):
        for i, layer in enumerate(network.layers):
            if _replaceable(layer):
                network.layers[i] = FastConvolution(layer)
    return processor
