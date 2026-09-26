"""Build the ChordChart Windows app.

    uv run python packaging/build.py            # app folder + zip
    uv run python packaging/build.py --installer # also the Inno Setup installer

Steps:
1. ffmpeg: a pinned LGPL build (BtbN), downloaded once into packaging/vendor/ and
   checked against its SHA-256; ffmpeg.exe is extracted from it.
2. Licenses of every bundled component (collect_licenses.py), then PyInstaller
   (packaging/chordchart.spec) -> packaging/dist/ChordChart/.
3. Zip that folder -> packaging/out/ChordChart-<version>-win64.zip.
4. With --installer: Inno Setup (packaging/installer.iss) ->
   packaging/out/ChordChart-Setup-<version>.exe.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
VENDOR, OUT = HERE / "vendor", HERE / "out"

FFMPEG_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-26-13-03/"
    "ffmpeg-n9.0.2-10-g51c4a23d74-win64-lgpl-9.0.zip"
)
FFMPEG_SHA256 = "ccde5efc9cf6e885cb418f4c0846865f0a7767752d42b1d955b901c41606b205"
FFMPEG_ZIP = VENDOR / "ffmpeg-n9.0.2-win64-lgpl.zip"


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from chordchart import __version__

    fetch_ffmpeg()
    subprocess.run([sys.executable, str(HERE / "collect_licenses.py")], check=True)
    pyinstaller()
    zip_path = OUT / f"ChordChart-{__version__}-win64.zip"
    make_zip(HERE / "dist" / "ChordChart", zip_path)
    print(f"app folder: {HERE / 'dist' / 'ChordChart'}\nzip:        {zip_path}")
    if "--installer" in sys.argv:
        setup = inno_setup(__version__)
        print(f"installer:  {setup}")
    return 0


def fetch_ffmpeg() -> None:
    VENDOR.mkdir(exist_ok=True)
    if not FFMPEG_ZIP.exists():
        print("downloading ffmpeg (LGPL build)...")
        urllib.request.urlretrieve(FFMPEG_URL, FFMPEG_ZIP)
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


def pyinstaller() -> None:
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
            str(HERE / "build"),
            str(HERE / "chordchart.spec"),
        ],
        check=True,
        cwd=ROOT,
    )


def make_zip(folder: Path, zip_path: Path) -> None:
    OUT.mkdir(exist_ok=True)
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, Path("ChordChart") / path.relative_to(folder))


def inno_setup(version: str) -> Path:
    iscc = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"
    if not iscc.exists():
        iscc = Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
    if not iscc.exists():
        raise SystemExit("Inno Setup not found; install: winget install JRSoftware.InnoSetup")
    subprocess.run(
        [str(iscc), "/Q", f"/DAppVersion={version}", str(HERE / "installer.iss")], check=True
    )
    return OUT / f"ChordChart-Setup-{version}.exe"


if __name__ == "__main__":
    sys.exit(main())
