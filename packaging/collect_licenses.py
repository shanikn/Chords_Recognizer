"""Collect the licenses of everything bundled in the app into
packaging/build/<variant>/licenses/ (the variant comes from CHORDCHART_VARIANT).

    THIRD-PARTY-NOTICES.txt   one summary: component, version, license, where from
    <component>/...           the full license texts shipped with each component

Python packages are found by walking ChordChart's runtime dependencies (with the extras
the app uses), minus what the spec excludes; the lite variant also leaves out every
package only the notes feature needs (variants.py). Components that aren't Python packages
(Python itself, ffmpeg, deno, madmom's models, the PyInstaller bootloader) are added
explicitly. build.py runs this before PyInstaller; the spec bundles the folder.
"""

from __future__ import annotations

import re
import shutil
import sys
from importlib import metadata
from pathlib import Path

import variants

HERE = Path(__file__).resolve().parent
VARIANT = variants.current()
TARGET = HERE / "build" / VARIANT.key / "licenses"
VENDOR = HERE / "vendor"

EXCLUDED = {"mutagen", "pytest", "pyinstaller", "pywebview", "deno", "lameenc", "sphn"}  # spec
# (the deno *package* only locates the binary; the deno binary gets its own entry below)


def runtime_distributions() -> list[metadata.Distribution]:
    excluded = set(EXCLUDED)
    if not VARIANT.notes:
        excluded |= {variants.normal(n) for n in variants.NOTES_ROOTS}
    seen = variants.reachable(["chordchart"], excluded)
    seen.pop("chordchart", None)
    return sorted(seen.values(), key=lambda d: _normal(d.metadata["Name"]))


def main() -> int:
    shutil.rmtree(TARGET, ignore_errors=True)
    TARGET.mkdir(parents=True)
    rows = []

    for dist in runtime_distributions():
        name, version = dist.metadata["Name"], dist.version
        license_name = (
            dist.metadata.get("License-Expression")
            or _short(dist.metadata.get("License"))
            or _classifier_license(dist)
            or "see license files"
        )
        folder = TARGET / f"{name}-{version}"
        copied = _copy_license_files(dist, folder)
        rows.append(
            (name, version, license_name, dist.metadata.get("Home-page") or _url(dist), copied)
        )

    import madmom  # its model files have their own, stricter license

    models_license = Path(madmom.__file__).parent / "models" / "LICENSE"
    _add_file(
        rows,
        "madmom models (pretrained networks)",
        "bundled with madmom",
        "CC BY-NC-SA 4.0 (non-commercial use only)",
        "https://github.com/CPJKU/madmom_models",
        models_license,
    )
    if VARIANT.notes:
        _add_demucs_weights(rows)
    _add_file(
        rows,
        "Python",
        sys.version.split()[0],
        "PSF-2.0",
        "https://www.python.org",
        Path(sys.base_prefix) / "LICENSE.txt",
    )
    _add_file(
        rows,
        "FFmpeg (ffmpeg.exe, LGPL build by BtbN)",
        "n9.0.2",
        "LGPL-2.1-or-later; source: https://ffmpeg.org/download.html and "
        "https://github.com/BtbN/FFmpeg-Builds",
        "https://ffmpeg.org",
        VENDOR / "ffmpeg-LICENSE.txt",
    )
    deno_version = metadata.version("deno")
    _add_text(
        rows,
        "Deno (deno.exe, JavaScript runtime)",
        deno_version,
        "MIT",
        "https://deno.com",
        DENO_MIT,
    )
    _add_text(
        rows,
        "PyInstaller bootloader",
        metadata.version("pyinstaller"),
        "GPL-2.0-or-later with the bootloader exception (allows distributing apps)",
        "https://pyinstaller.org",
        PYINSTALLER_NOTE,
    )

    width = max(len(r[0]) for r in rows)
    lines = [
        "ChordChart - third-party components",
        "",
        "ChordChart bundles the components below. Each keeps its own license; the full",
        "texts are in the folders next to this file. The chord, beat and key models come",
        "from madmom and are licensed CC BY-NC-SA 4.0: non-commercial use only.",
        "",
    ]
    for name, version, license_name, url, files in rows:
        lines.append(f"{name.ljust(width)}  {version:<14} {license_name}")
        if url:
            lines.append(f"{'':{width}}  {url}")
        if not files:
            lines.append(f"{'':{width}}  (no license file shipped with this package)")
    (TARGET / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    missing = [r[0] for r in rows if not r[4]]
    print(f"{len(rows)} components -> {TARGET}")
    if missing:
        print("no license file found for:", ", ".join(missing))
    return 0


def _copy_license_files(dist, folder: Path) -> list[str]:
    copied = []
    for file in dist.files or []:
        text = str(file).replace("\\", "/")
        base = text.rsplit("/", 1)[-1].upper()
        in_dist_info = ".dist-info/" in text
        if (
            in_dist_info
            and (
                "LICEN" in base
                or "COPYING" in base
                or "NOTICE" in base
                or "/licenses/" in text.lower()
            )
        ) or (base.startswith(("LICENSE", "LICENCE", "COPYING")) and text.count("/") <= 1):
            src = Path(dist.locate_file(file))
            if src.is_file():
                folder.mkdir(parents=True, exist_ok=True)
                dst = folder / src.name
                if dst.exists():
                    dst = folder / f"{src.parent.name}-{src.name}"
                shutil.copy2(src, dst)
                copied.append(dst.name)
    return copied


def _add_file(rows, name, version, license_name, url, path: Path) -> None:
    folder = TARGET / re.sub(r"[^\w.-]+", "-", name).strip("-")
    files = []
    if path.is_file():
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, folder / path.name)
        files = [path.name]
    rows.append((name, version, license_name, url, files))


def _add_text(rows, name, version, license_name, url, text: str) -> None:
    folder = TARGET / re.sub(r"[^\w.-]+", "-", name).strip("-")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "LICENSE.txt").write_text(text, encoding="utf-8")
    rows.append((name, version, license_name, url, ["LICENSE.txt"]))


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _short(text: str | None) -> str | None:
    if not text:
        return None
    first = text.strip().splitlines()[0]
    return first if len(first) <= 60 else None


def _classifier_license(dist) -> str | None:
    found = [
        c.split("::")[-1].strip()
        for c in dist.metadata.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    return ", ".join(found) or None


def _url(dist) -> str | None:
    for entry in dist.metadata.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        if label.strip().lower() in ("homepage", "home", "source", "repository"):
            return url.strip()
    return None


DENO_MIT = """MIT License

Copyright 2018-2026 the Deno authors

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be included in all copies
or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""

PYINSTALLER_NOTE = """ChordChart.exe starts through the PyInstaller bootloader.

PyInstaller is distributed under the GNU General Public License, version 2 or later,
with a special exception that allows the bootloader to be distributed as part of
applications built with PyInstaller, under any license:
https://github.com/pyinstaller/pyinstaller/blob/develop/COPYING.txt
"""


def _add_demucs_weights(rows) -> None:
    """Not bundled, but the full app downloads and runs them, so they get a notice."""
    from chordchart.notes import stems

    _add_text(
        rows,
        "Demucs htdemucs_6s model weights (downloaded on first use, not bundled)",
        stems.MODEL_REVISION[:12],
        "MIT",
        f"https://huggingface.co/{stems.MODEL_REPO}",
        "The Demucs weights are MIT-licensed, like Demucs itself "
        "(https://github.com/adefossez/demucs). ChordChart downloads them from the "
        f"Hugging Face hub ({stems.MODEL_REPO}) the first time notes are transcribed.\n",
    )


if __name__ == "__main__":
    sys.exit(main())
