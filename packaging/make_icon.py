"""Draw packaging/chordchart.ico: a chord diagram (strings, frets, finger dots) in white
on the app's green, rounded square. numpy only; rerun after changing the design.

    uv run python packaging/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

GREEN = (47, 111, 79)
WHITE = (255, 255, 255)
SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4


def draw(size: int) -> np.ndarray:
    """RGBA image, drawn at 4x and averaged down for smooth edges."""
    n = size * SUPERSAMPLE
    y, x = np.mgrid[0:n, 0:n] / n  # 0..1 coordinates
    alpha = _rounded_square(x, y, radius=0.2)
    white = np.zeros_like(x, dtype=bool)
    thin = 0.035 if size >= 32 else 0.06
    strings = np.linspace(0.27, 0.73, 4)  # 4 strings (ukulele-like reads better small)
    for sx in strings:
        white |= (np.abs(x - sx) < thin / 2) & (y > 0.24) & (y < 0.82)
    for fy in (0.24, 0.43, 0.62, 0.82):  # nut + 3 frets
        white |= (
            (np.abs(y - fy) < (thin * 1.6 if fy == 0.24 else thin) / 2) & (x > 0.25) & (x < 0.75)
        )
    for sx, fy in ((strings[0], 0.525), (strings[2], 0.335), (strings[3], 0.715)):  # fingers
        white |= (x - sx) ** 2 + (y - fy) ** 2 < 0.068**2
    rgb = np.where(white[..., None], WHITE, GREEN).astype(float)
    rgba = np.dstack([rgb, alpha * 255])
    return rgba.reshape(size, SUPERSAMPLE, size, SUPERSAMPLE, 4).mean(axis=(1, 3)).astype(np.uint8)


def _rounded_square(x, y, radius):
    cx = np.clip(x, radius, 1 - radius)
    cy = np.clip(y, radius, 1 - radius)
    return ((x - cx) ** 2 + (y - cy) ** 2 <= radius**2).astype(float)


def png(rgba: np.ndarray) -> bytes:
    h, w, _ = rgba.shape
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def ico(images: list[bytes], sizes: tuple[int, ...]) -> bytes:
    """ICO file with PNG-compressed entries (supported since Windows Vista)."""
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in zip(sizes, images, strict=True):
        dim = 0 if size >= 256 else size
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)


if __name__ == "__main__":
    images = [png(draw(s)) for s in SIZES]
    target = Path(__file__).with_name("chordchart.ico")
    target.write_bytes(ico(images, SIZES))
    Path(__file__).with_name("chordchart-256.png").write_bytes(images[-1])  # for a quick look
    print(f"wrote {target} ({target.stat().st_size} bytes)")
