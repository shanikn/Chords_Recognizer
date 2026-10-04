"""Write the Android launcher icon: the "Soundhole" design, a guitar soundhole on peach
with six strings and three coral finger dots.

- Android 8+: an adaptive icon (peach background + the soundhole as a vector), so each
  launcher can mask it to its own shape.
- Android 7: PNGs per screen density, a rounded square and a circle.
- Google Play: the 512 px store icon (square; Play rounds it) and the 1024x500 feature
  graphic, into docs/play/.

Rerun after changing the design below (needs cairosvg and Pillow):

    python3 android/tools/make_icon.py
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import cairosvg
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "android" / "app" / "src" / "main" / "res"
PLAY = ROOT / "android" / "docs" / "play"
DENSITIES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}

# The design, on a 1024-unit square.
PEACH, INK, CREAM, CORAL = "#F2C6B0", "#2A1820", "#FFF6EC", "#FF8A5C"
C = 512
# rosette: (radius, stroke width) of the two plain rings; the dashed ring between them
RINGS = ((372, 6), (320, 6))
DASH_RING, DASH_WIDTH, DASH_LEN, DASH_PERIOD = 346, 18, 5, 20
HOLE = 286
STRINGS = ((392, 13), (440, 11), (488, 9), (536, 7), (584, 6), (632, 5))  # (x, width), low E first
DOTS = ((440, 610), (488, 512), (584, 414))
DOT_R, DOT_STROKE = 30, 8

# The adaptive icon's 108 dp canvas: the design square maps onto its middle 72 dp (what
# most launchers show), which keeps the rosette inside the 66 dp safe zone.
OFFSET, SCALE = 18.0, 72.0 / 1024


def _dashes() -> list[list[tuple[float, float]]]:
    """The dashed ring as small radial quads (vector drawables have no dash patterns)."""
    count = round(2 * math.pi * DASH_RING / DASH_PERIOD)
    half = DASH_LEN / 2 / DASH_RING  # half a dash, as an angle
    r0, r1 = DASH_RING - DASH_WIDTH / 2, DASH_RING + DASH_WIDTH / 2
    quads = []
    for i in range(count):
        a = 2 * math.pi * i / count
        quads.append([
            (C + r * math.cos(a + s * half), C + r * math.sin(a + s * half))
            for r, s in ((r0, -1), (r1, -1), (r1, 1), (r0, 1))
        ])
    return quads


def svg(rounded: float = 0) -> str:
    """The design as an SVG, for the PNGs; `rounded` is the corner radius (0 = square)."""
    quads = " ".join(
        "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in q) + " Z" for q in _dashes()
    )
    parts = [
        f'<rect width="1024" height="1024" rx="{rounded}" fill="{PEACH}"/>',
        *(f'<circle cx="{C}" cy="{C}" r="{r}" fill="none" stroke="{INK}" stroke-width="{w}"/>'
          for r, w in RINGS),
        f'<path d="{quads}" fill="{INK}"/>',
        f'<circle cx="{C}" cy="{C}" r="{HOLE}" fill="{INK}"/>',
        *(f'<line x1="{x}" y1="-10" x2="{x}" y2="1034" stroke="{CREAM}" stroke-width="{w}" '
          'stroke-linecap="round"/>' for x, w in STRINGS),
        *(f'<circle cx="{x}" cy="{y}" r="{DOT_R}" fill="{CORAL}" stroke="{INK}" '
          f'stroke-width="{DOT_STROKE}"/>' for x, y in DOTS),
    ]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">'
        f'<clipPath id="c"><rect width="1024" height="1024" rx="{rounded}"/></clipPath>'
        f'<g clip-path="url(#c)">{"".join(parts)}</g></svg>'
    )


def _png(size: int, rounded: float = 0, circle: bool = False) -> bytes:
    big = size * 4
    img = Image.open(io.BytesIO(cairosvg.svg2png(bytestring=svg(rounded).encode(),
                                                 output_width=big, output_height=big)))
    img = img.convert("RGBA")
    if circle:
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
        img.putalpha(mask)
    out = io.BytesIO()
    img.resize((size, size), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def feature_svg() -> str:
    """Google Play's feature graphic (1024x500): the soundhole, its strings running the full
    height, and the app's name."""
    scale = 0.56
    tx, ty = 240 - C * scale, 250 - C * scale
    design = svg().split('<g clip-path="url(#c)">', 1)[1].rsplit("</g>", 1)[0]
    design = design.split("/>", 1)[1]  # without the square background
    design = design.replace('y1="-10"', f'y1="{-ty / scale - 10:.0f}"').replace(
        'y2="1034"', f'y2="{(500 - ty) / scale + 10:.0f}"')
    font = "DejaVu Sans, Liberation Sans, Arial, sans-serif"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="500" viewBox="0 0 1024 500">'
        f'<rect width="1024" height="500" fill="{PEACH}"/>'
        f'<g transform="translate({tx:.1f},{ty:.1f}) scale({scale})">{design}</g>'
        f'<text x="490" y="232" font-family="{font}" font-weight="bold" font-size="62" fill="{INK}">Chord Chart</text>'
        f'<text x="492" y="290" font-family="{font}" font-size="26" fill="{INK}">Chords, beats and bars of a song,</text>'
        f'<text x="492" y="326" font-family="{font}" font-size="26" fill="{INK}">worked out on your phone.</text>'
        "</svg>"
    )


def _p(x: float, y: float) -> str:
    return f"{OFFSET + SCALE * x:.2f},{OFFSET + SCALE * y:.2f}"


def _circle(r: float) -> str:
    rr = f"{SCALE * r:.2f}"
    return f"M{_p(C - r, C)} a{rr},{rr} 0 1,0 {2 * SCALE * r:.2f},0 a{rr},{rr} 0 1,0 {-2 * SCALE * r:.2f},0 Z"


def _dot(x: float, y: float) -> str:
    rr = f"{SCALE * DOT_R:.2f}"
    return (f"M{_p(x - DOT_R, y)} a{rr},{rr} 0 1,0 {2 * SCALE * DOT_R:.2f},0 "
            f"a{rr},{rr} 0 1,0 {-2 * SCALE * DOT_R:.2f},0 Z")


def foreground_xml() -> str:
    """The soundhole on a transparent 108 dp canvas; the strings run off its edges."""
    def path(data: str, **attrs: str) -> str:
        extra = "".join(f'\n        android:{k}="{v}"' for k, v in attrs.items())
        return f'    <path android:pathData="{data}"{extra} />\n'

    out = [
        "<!-- Generated by android/tools/make_icon.py -->\n",
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    android:width="108dp" android:height="108dp"\n'
        '    android:viewportWidth="108" android:viewportHeight="108">\n',
    ]
    for r, w in RINGS:
        out.append(path(_circle(r), strokeColor=INK, strokeWidth=f"{SCALE * w:.2f}"))
    out.append(path(" ".join("M" + " L".join(_p(x, y) for x, y in q) + " Z" for q in _dashes()),
                    fillColor=INK))
    out.append(path(_circle(HOLE), fillColor=INK))
    top, bottom = -OFFSET / SCALE, 1024 + OFFSET / SCALE  # the canvas's edges, in design units
    for x, w in STRINGS:
        out.append(path(f"M{_p(x, top)} L{_p(x, bottom)}", strokeColor=CREAM,
                        strokeWidth=f"{SCALE * w:.2f}", strokeLineCap="round"))
    for x, y in DOTS:
        out.append(path(_dot(x, y), fillColor=CORAL, strokeColor=INK,
                        strokeWidth=f"{SCALE * DOT_STROKE:.2f}"))
    out.append("</vector>\n")
    return "".join(out)


ADAPTIVE = """<!-- Generated by android/tools/make_icon.py -->
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
"""


def main() -> None:
    for density, size in DENSITIES.items():
        folder = RES / f"mipmap-{density}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "ic_launcher.png").write_bytes(_png(size, rounded=1024 * 0.2))
        (folder / "ic_launcher_round.png").write_bytes(_png(size, circle=True))
    (RES / "drawable").mkdir(exist_ok=True)
    (RES / "drawable" / "ic_launcher_foreground.xml").write_text(foreground_xml(), newline="\n")
    anydpi = RES / "mipmap-anydpi-v26"
    anydpi.mkdir(exist_ok=True)
    for name in ("ic_launcher.xml", "ic_launcher_round.xml"):
        (anydpi / name).write_text(ADAPTIVE, newline="\n")
    (RES / "values" / "ic_launcher_background.xml").write_text(
        "<!-- Generated by android/tools/make_icon.py -->\n<resources>\n"
        f'    <color name="ic_launcher_background">{PEACH}</color>\n</resources>\n',
        newline="\n",
    )
    PLAY.mkdir(parents=True, exist_ok=True)
    (PLAY / "icon-512.png").write_bytes(_png(512))
    feature = io.BytesIO(cairosvg.svg2png(bytestring=feature_svg().encode(), output_width=1024, output_height=500))
    Image.open(feature).convert("RGB").save(PLAY / "feature-graphic.png", "PNG", optimize=True)
    print(f"wrote the launcher icon into {RES}, the Play icon and feature graphic into {PLAY}")


if __name__ == "__main__":
    main()
