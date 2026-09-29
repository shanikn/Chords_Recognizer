"""Build the ChordChart Windows app, in one of two variants (variants.py).

    uv run python packaging/build.py                  # lite: "ChordChart", chords only
    uv run python packaging/build.py --variant full   # "ChordChart Notes": chords + notes
    uv run python packaging/build.py --installer      # also the Inno Setup installer
    uv run python packaging/build.py --installer --release   # the one to hand out

Steps:
1. ffmpeg: a pinned LGPL build (BtbN), downloaded once into packaging/vendor/ and
   checked against its SHA-256; ffmpeg.exe is extracted from it.
2. Licenses of every bundled component (collect_licenses.py), then PyInstaller
   (packaging/chordchart.spec) -> packaging/dist/<app name>/.
3. The built app's own self-test (ChordChart.exe --self-test): every bundled piece
   loads and a short analysis gives the expected chords; a failure stops the build.
   secrets_check.py: the folder must not hold this PC's Spotify key or per-user files
   (settings.json, caches). Then zip it ->
   packaging/out/<ChordChart|ChordChartNotes>-<version>-win64.zip.
4. With --installer: Inno Setup (packaging/installer.iss) ->
   packaging/out/<ChordChart|ChordChartNotes>-Setup-<version>.exe. Test builds compress
   it with lzma2/fast; --release uses lzma2/max (smaller download, slower to build).
Each variant has its own work folder, packaging/build/<variant>/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import secrets_check  # noqa: E402
import variants  # noqa: E402

VENDOR, OUT = HERE / "vendor", HERE / "out"
TEST_COMPRESSION = "lzma2/fast"  # installer compression for test builds
RELEASE_COMPRESSION = "lzma2/max"  # --release: the installer people download

FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-26-13-03/"
    "ffmpeg-n9.0.2-10-g51c4a23d74-win64-lgpl-9.0.zip"
)
FFMPEG_SHA256 = "ccde5efc9cf6e885cb418f4c0846865f0a7767752d42b1d955b901c41606b205"
FFMPEG_ZIP = VENDOR / "ffmpeg-n9.0.2-win64-lgpl.zip"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ChordChart Windows app.")
    parser.add_argument("--variant", choices=sorted(variants.VARIANTS), default=variants.DEFAULT)
    parser.add_argument("--installer", action="store_true", help="also build the installer")
    parser.add_argument(
        "--release",
        action="store_true",
        help="installer compressed with lzma2/max (default: lzma2/fast, for test builds)",
    )
    args = parser.parse_args()
    variant = variants.VARIANTS[args.variant]
    os.environ[variants.ENV] = variant.key  # read by the spec and collect_licenses.py

    sys.path.insert(0, str(ROOT))
    from chordchart import __version__

    fetch_ffmpeg()
    subprocess.run([sys.executable, str(HERE / "collect_licenses.py")], check=True)
    pyinstaller(variant)
    folder = HERE / "dist" / variant.app_name
    self_test(folder)
    secrets_check.check(folder)  # before anything is packed from it
    zip_path = OUT / f"{variant.file_stem}-{__version__}-win64.zip"
    make_zip(folder, zip_path, variant.app_name)
    print(f"{variant.app_name} ({variant.key})")
    print(f"app folder: {folder}\nzip:        {zip_path}")
    if args.installer:
        compression = RELEASE_COMPRESSION if args.release else TEST_COMPRESSION
        began = time.perf_counter()
        setup = inno_setup(__version__, variant, compression)
        took = time.perf_counter() - began
        print(f"installer:  {setup} ({compression}, {took:.0f} s)")
    return 0


def fetch_ffmpeg() -> None:
    VENDOR.mkdir(exist_ok=True)
    if not FFMPEG_ZIP.exists():
        print("downloading ffmpeg (LGPL build)...")
        # Via .part, so an interrupted download never leaves a truncated zip behind.
        partial = FFMPEG_ZIP.with_suffix(".part")
        urllib.request.urlretrieve(FFMPEG_URL, partial)
        partial.replace(FFMPEG_ZIP)
    digest = hashlib.sha256(FFMPEG_ZIP.read_bytes()).hexdigest()
    if digest != FFMPEG_SHA256:
        FFMPEG_ZIP.unlink()
        raise SystemExit(f"ffmpeg download failed its SHA-256 check ({digest}); deleted it")
    with zipfile.ZipFile(FFMPEG_ZIP) as archive:
        for name in archive.namelist():
            base = name.rsplit("/", 1)[-1]
            if base == "ffmpeg.exe":
                (VENDOR / "ffmpeg.exe").write_bytes(archive.read(name))
            elif name.endswith("/LICENSE.txt") and name.count("/") == 1:
                (VENDOR / "ffmpeg-LICENSE.txt").write_bytes(archive.read(name))
    if not (VENDOR / "ffmpeg.exe").exists():
        raise SystemExit("ffmpeg.exe not found in the ffmpeg zip")


def pyinstaller(variant: variants.Variant) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--log-level",
            "WARN",
            "--distpath",
            str(HERE / "dist"),
            "--workpath",
            str(HERE / "build" / variant.key),
            str(HERE / "chordchart.spec"),
        ],
        check=True,
        cwd=ROOT,
    )


def self_test(folder: Path) -> None:
    """Run the built app's self-test; stop the build if any check fails. It loads what
    the app loads at run time (ffmpeg, deno, yt-dlp, the models, the window's .NET
    runtime and WebView2 backend, and in the full app torch and basic-pitch), so a package
    PyInstaller left out fails here instead of on someone's PC."""
    report_path = HERE / "build" / f"selftest-{folder.name.replace(' ', '-')}.json"
    report_path.unlink(missing_ok=True)
    subprocess.run([str(folder / "ChordChart.exe"), "--self-test", str(report_path)], timeout=900)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(f"the built app's self-test wrote no report ({report_path})") from None
    failed = {name: c for name, c in report["checks"].items() if not c["ok"]}
    for name, check in failed.items():
        print(f"self-test FAILED: {name}: {check['detail']}\n{check.get('traceback', '')}")
    if failed:
        raise SystemExit(
            f"the built app failed its self-test ({', '.join(failed)}); see {report_path}"
        )
    print(f"self-test: OK ({len(report['checks'])} checks)")


def make_zip(folder: Path, zip_path: Path, top: str) -> None:
    OUT.mkdir(exist_ok=True)
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, Path(top) / path.relative_to(folder))


def inno_setup(
    version: str, variant: variants.Variant, compression: str = TEST_COMPRESSION
) -> Path:
    from chordchart.desktop.app import instance_name
    from chordchart.desktop.instance import mutex_name

    other = next(v for v in variants.VARIANTS.values() if v.key != variant.key)
    iscc = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"
    if not iscc.exists():
        iscc = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
    if not iscc.exists():
        raise SystemExit("Inno Setup not found; install: winget install JRSoftware.InnoSetup")
    defines = {
        "AppVersion": version,
        "AppName": variant.app_name,
        "AppId": variant.app_id,
        "FileStem": variant.file_stem,
        "Variant": variant.key,
        # The other variant shares the data folder: its uninstall key keeps it safe.
        "OtherAppName": other.app_name,
        "OtherAppKey": other.app_id.replace("{{", "{", 1) + "_is1",
        # Held while the app runs (desktop/instance.py): Setup and the uninstaller ask
        # to close it first instead of failing on files in use.
        "AppMutex": mutex_name(instance_name(variant.notes)),
        "Compression": compression,
    }
    args = [f"/D{name}={value}" for name, value in defines.items()]
    subprocess.run([str(iscc), "/Q", *args, str(HERE / "installer.iss")], check=True)
    return OUT / f"{variant.file_stem}-Setup-{version}.exe"


if __name__ == "__main__":
    sys.exit(main())
