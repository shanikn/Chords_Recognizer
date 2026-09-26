"""Build the ChordChart Windows app, in one of two variants (variants.py).

    uv run python packaging/build.py                  # lite: "ChordChart", chords only
    uv run python packaging/build.py --variant full   # "ChordChart Notes": chords + notes
    uv run python packaging/build.py --installer      # also the Inno Setup installer

Steps:
1. ffmpeg: a pinned LGPL build (BtbN), downloaded once into packaging/vendor/ and
   checked against its SHA-256; ffmpeg.exe is extracted from it.
2. Licenses of every bundled component (collect_licenses.py), then PyInstaller
   (packaging/chordchart.spec) -> packaging/dist/<app name>/.
3. Zip that folder -> packaging/out/<ChordChart|ChordChartNotes>-<version>-win64.zip.
4. With --installer: Inno Setup (packaging/installer.iss) ->
   packaging/out/<ChordChart|ChordChartNotes>-Setup-<version>.exe.
Each variant has its own work folder, packaging/build/<variant>/.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import variants  # noqa: E402

VENDOR, OUT = HERE / "vendor", HERE / "out"

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
    args = parser.parse_args()
    variant = variants.VARIANTS[args.variant]
    os.environ[variants.ENV] = variant.key  # read by the spec and collect_licenses.py

    sys.path.insert(0, str(ROOT))
    from chordchart import __version__

    fetch_ffmpeg()
    subprocess.run([sys.executable, str(HERE / "collect_licenses.py")], check=True)
    pyinstaller(variant)
    folder = HERE / "dist" / variant.app_name
    zip_path = OUT / f"{variant.file_stem}-{__version__}-win64.zip"
    make_zip(folder, zip_path, variant.app_name)
    print(f"{variant.app_name} ({variant.key})")
    print(f"app folder: {folder}\nzip:        {zip_path}")
    if args.installer:
        setup = inno_setup(__version__, variant)
        print(f"installer:  {setup}")
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


def make_zip(folder: Path, zip_path: Path, top: str) -> None:
    OUT.mkdir(exist_ok=True)
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, Path(top) / path.relative_to(folder))


def inno_setup(version: str, variant: variants.Variant) -> Path:
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
    }
    args = [f"/D{name}={value}" for name, value in defines.items()]
    subprocess.run([str(iscc), "/Q", *args, str(HERE / "installer.iss")], check=True)
    return OUT / f"{variant.file_stem}-Setup-{version}.exe"


if __name__ == "__main__":
    sys.exit(main())
