"""Download the C++ core's third-party code into android/third_party (git-ignored).

    uv run python android/tools/fetch_deps.py

Pinned versions, each checked against its SHA-256 (a mismatch deletes the file and
stops):

- pocketfft (C++ header-only; the FFT numpy and scipy use), BSD-3-Clause
- libsoxr, the fork and commit python-soxr 1.1.0 bundles (dofuuz/soxr a66f3ee), so the
  Beat This! frontend resamples exactly like the Python pipeline. LGPL-2.1.
- libopus 1.5, the release Android 15 uses (external/libopus), built fixed-point as AOSP
  builds it, so Opus decodes in-process exactly as Android's own decoder does. BSD-3-Clause.
- Android 15's own MP3 and AAC decoders (AOSP tag android-15.0.0_r1), so MP3 and AAC decode
  in-process exactly as the phone's software decoders (c2.android.mp3/aac.decoder) do:
  the PacketVideo MP3 decoder (frameworks/av media/module/codecs/mp3dec, Apache-2.0), the
  Fraunhofer FDK AAC decoder (external/aac, the "Software License for The Fraunhofer FDK
  AAC Codec Library for Android": free redistribution with its licence text and source;
  no patent licence), and Codec2's DRC helper for it (media/codec2/components/aac,
  Apache-2.0). android.googlesource.com makes these archives on the fly (their bytes
  differ between downloads), so they're pinned by a hash of the extracted files instead.
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
import time
import urllib.error
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
AOSP_TAG = "android-15.0.0_r1"
GITILES = "https://android.googlesource.com"

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
# Directory archives from android.googlesource.com: name -> (url, subdirectories kept).
FRAMEWORKS_AV = f"{GITILES}/platform/frameworks/av/+archive/refs/tags/{AOSP_TAG}"
TREES = {
    "aosp-mp3dec": (f"{FRAMEWORKS_AV}/media/module/codecs/mp3dec.tar.gz",
                    ["src", "include", "NOTICE", "patent_disclaimer.txt"]),
    "aosp-aac": (f"{GITILES}/platform/external/aac/+archive/refs/tags/{AOSP_TAG}.tar.gz",
                 ["libAACdec", "libFDK", "libSYS", "libMpegTPDec", "libSBRdec", "libArithCoding",
                  "libDRCdec", "libSACdec", "libPCMutils", "NOTICE"]),
    "aosp-c2aac": (f"{FRAMEWORKS_AV}/media/codec2/components/aac.tar.gz",
                   ["DrcPresModeWrap.cpp", "DrcPresModeWrap.h", "C2SoftAacDec.cpp", "NOTICE"]),
}  # fmt: skip
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
        # android.googlesource.com builds archives on demand and answers 503 or 429 now
        # and then (seen on GitHub's runners): try again a few times before giving up.
        for attempt in range(5):
            try:
                with (
                    urllib.request.urlopen(url, timeout=120) as response,
                    partial.open("wb") as out,
                ):
                    shutil.copyfileobj(response, out)
                break
            except (urllib.error.URLError, TimeoutError) as error:
                retriable = not isinstance(error, urllib.error.HTTPError) or error.code in (
                    429,
                    500,
                    502,
                    503,
                    504,
                )
                if attempt == 4 or not retriable:
                    raise
                wait = 10 * 2**attempt
                print(f"  {error}; retrying in {wait} s", flush=True)
                time.sleep(wait)
        partial.replace(path)
    return path


def tree_hash(root: Path) -> str:
    """SHA-256 over every file's relative path and SHA-256, in sorted order.

    Sorted as Windows sorts paths (per part, case-insensitively), where the pinned hashes
    were made, so the same files give the same hash on Linux too.
    """
    digest = hashlib.sha256()
    files = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    for rel in sorted(files, key=lambda rel: rel.lower().split("/")):
        path = root / rel
        digest.update(f"{rel}\0{hashlib.sha256(path.read_bytes()).hexdigest()}\n".encode())
    return digest.hexdigest()


def fetch_tree(
    name: str, url: str, keep: list[str], hashes: dict[str, str], printing: bool
) -> None:
    target = THIRD_PARTY / name
    if target.exists() and not printing:
        return
    archive_path = fetch(f"{name}.tar.gz", url)
    staging = THIRD_PARTY / f"{name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(archive_path) as archive:
        members = [m for m in archive.getmembers() if m.name.split("/")[0] in keep]
        archive.extractall(staging, members=members, filter="data")
    digest = tree_hash(staging)
    archive_path.unlink()  # not byte-stable: keep only the checked tree
    if printing:
        print(f"{digest}  {name}")
        shutil.rmtree(staging)
        return
    if hashes.get(name) != digest:
        shutil.rmtree(staging)
        raise SystemExit(f"{name}: tree SHA-256 {digest} is not the pinned one; deleted it")
    staging.rename(target)


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
    for name, (url, keep) in TREES.items():
        fetch_tree(name, url, keep, hashes, printing)
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
