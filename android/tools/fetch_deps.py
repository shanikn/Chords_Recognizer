"""Download the C++ core's third-party code into android/third_party (git-ignored).

    uv run python android/tools/fetch_deps.py

Pinned versions, each checked against its SHA-256 (a mismatch deletes the file and
stops):

- pocketfft (C++ header-only; the FFT numpy and scipy use), BSD-3-Clause
- libsoxr, the fork and commit python-soxr 1.1.0 bundles (dofuuz/soxr a66f3ee), so the
  Beat This! frontend resamples exactly like the Python pipeline. LGPL-2.1.
- libopus 1.5, the release Android 15 uses (external/libopus), built fixed-point as AOSP
  builds it, so Opus decodes in-process exactly as Android's own decoder does. BSD-3-Clause.
- ONNX Runtime 1.30.0 (the Python pipeline's version): the Windows x64 build for the core's
  PC tests, and the Android package (AAR: headers and libonnxruntime.so per ABI). MIT.

Set CHORDCHART_PRINT_HASHES=1 to print the hashes instead of checking them (when bumping
a version).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY = ROOT / "third_party"
DOWNLOADS = THIRD_PARTY / "downloads"

POCKETFFT_COMMIT = "c90e55b3d529f8efa40ed01a20de22405f45fc65"
SOXR_COMMIT = "a66f3eeeeb62a32403ff143b756eed92b1ec6b62"
ORT_VERSION = "1.30.0"
OPUS_VERSION = "1.5"

DEPS = {
    "pocketfft_hdronly.h": (
        f"https://raw.githubusercontent.com/mreineck/pocketfft/{POCKETFFT_COMMIT}/pocketfft_hdronly.h",
        "",
    ),
    "soxr.tar.gz": (f"https://codeload.github.com/dofuuz/soxr/tar.gz/{SOXR_COMMIT}", ""),
    "opus.tar.gz": (f"https://downloads.xiph.org/releases/opus/opus-{OPUS_VERSION}.tar.gz", ""),
    "onnxruntime-win-x64.zip": (
        f"https://github.com/microsoft/onnxruntime/releases/download/v{ORT_VERSION}/"
        f"onnxruntime-win-x64-{ORT_VERSION}.zip",
        "",
    ),
    "onnxruntime-android.aar": (
        f"https://repo1.maven.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/"
        f"{ORT_VERSION}/onnxruntime-android-{ORT_VERSION}.aar",
        "",
    ),
}
HASHES_FILE = Path(__file__).with_name("deps.sha256")


def pinned() -> dict[str, str]:
    if not HASHES_FILE.exists():
        return {}
    pairs = (line.split() for line in HASHES_FILE.read_text().splitlines() if line.strip())
    return {name: digest for digest, name in pairs}


def fetch(name: str, url: str) -> Path:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    path = DOWNLOADS / name
    if not path.exists():
        print(f"downloading {name}...", flush=True)
        partial = path.with_suffix(path.suffix + ".part")
        with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
        partial.replace(path)
    return path


def main() -> int:
    hashes = pinned()
    printing = os.environ.get("CHORDCHART_PRINT_HASHES") == "1"
    paths = {}
    for name, (url, _) in DEPS.items():
        path = fetch(name, url)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if printing:
            print(f"{digest}  {name}")
        elif hashes.get(name) != digest:
            path.unlink()
            raise SystemExit(f"{name}: SHA-256 {digest} is not the pinned one; deleted it")
        paths[name] = path
    if printing:
        return 0

    (THIRD_PARTY / "pocketfft").mkdir(exist_ok=True)
    shutil.copy2(paths["pocketfft_hdronly.h"], THIRD_PARTY / "pocketfft" / "pocketfft_hdronly.h")

    soxr_dir = THIRD_PARTY / "soxr"
    if not soxr_dir.exists():
        with tarfile.open(paths["soxr.tar.gz"]) as archive:
            top = archive.getnames()[0].split("/")[0]
            archive.extractall(THIRD_PARTY, filter="data")
        (THIRD_PARTY / top).rename(soxr_dir)

    opus_dir = THIRD_PARTY / "opus"
    if not opus_dir.exists():
        with tarfile.open(paths["opus.tar.gz"]) as archive:
            top = archive.getnames()[0].split("/")[0]
            archive.extractall(THIRD_PARTY, filter="data")
        (THIRD_PARTY / top).rename(opus_dir)

    ort_win = THIRD_PARTY / "onnxruntime-win-x64"
    if not ort_win.exists():
        with zipfile.ZipFile(paths["onnxruntime-win-x64.zip"]) as archive:
            top = archive.namelist()[0].split("/")[0]
            archive.extractall(THIRD_PARTY)
        (THIRD_PARTY / top).rename(ort_win)

    ort_android = THIRD_PARTY / "onnxruntime-android"
    if not ort_android.exists():
        with zipfile.ZipFile(paths["onnxruntime-android.aar"]) as archive:
            for member in archive.namelist():
                if member.startswith(("headers/", "jni/")) and not member.endswith("/"):
                    target = ort_android / member
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
    print(f"third-party code in {THIRD_PARTY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
