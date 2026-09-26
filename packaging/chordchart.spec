# PyInstaller spec for the ChordChart Windows app. Build with: uv run python packaging/build.py
# (build.py fetches ffmpeg first). One folder (not onefile): starts faster and trips
# fewer antivirus heuristics.

import os

import basic_pitch
from deno import find_deno_bin
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821 (SPECPATH is set by PyInstaller)
VENDOR = os.path.join(SPECPATH, "vendor")  # noqa: F821

datas = []
datas += collect_data_files("madmom")  # the pickled models (and their LICENSE)
datas += collect_data_files("yt_dlp_ejs")  # YouTube's JavaScript challenge solver
datas += [(os.path.join(ROOT, "chordchart", "web", "index.html"), "chordchart/web")]
# basic-pitch ships its model in four formats; the app runs only the ONNX one (2 MB).
# Demucs's weights aren't bundled: they're downloaded on first use (notes/stems.py).
BP_MODELS = os.path.join(os.path.dirname(basic_pitch.__file__), "saved_models", "icassp_2022")
datas += [(os.path.join(BP_MODELS, "nmp.onnx"), "basic_pitch/saved_models/icassp_2022")]
licenses = os.path.join(SPECPATH, "build", "licenses")  # noqa: F821 (collect_licenses.py)
if os.path.isdir(licenses):
    datas += [(licenses, "licenses")]

binaries = [
    (os.path.join(VENDOR, "ffmpeg.exe"), "bin"),  # LGPL build, see build.py
    (find_deno_bin(), "bin"),  # JavaScript runtime yt-dlp needs for YouTube
]

hiddenimports = (
    collect_submodules("madmom")  # the pickled models refer to madmom classes by name
    + collect_submodules("yt_dlp")
    + collect_submodules("yt_dlp_ejs")
    + collect_submodules("uvicorn")
    + ["chordchart.desktop.selftest", "chordchart.pipeline", "chordchart.processors"]
    # Notes (imported lazily). Demucs loads its model class by name from the weights'
    # metadata, so those modules are listed explicitly.
    + ["chordchart.notes.pipeline", "chordchart.notes.midi", "chordchart.notes.stems"]
    + ["chordchart.notes.transcribe", "demucs.htdemucs", "demucs.hdemucs", "demucs.demucs"]
)

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "chordchart", "desktop", "app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    # mutagen is GPL and yt-dlp only uses it to tag files; pywebview comes in round 2.
    # lameenc (LGPL, MP3 encoding) and sphn are only used by Demucs's file writing,
    # which the app doesn't use; the others are model runtimes basic-pitch can't use here.
    excludes=[
        "mutagen", "pytest", "tkinter", "webview", "PyInstaller", "IPython",
        "lameenc", "sphn", "tensorflow", "coremltools", "tflite_runtime", "matplotlib",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
icon = os.path.join(SPECPATH, "chordchart.ico")  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChordChart",
    console=False,  # no terminal window for her
    icon=icon if os.path.exists(icon) else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ChordChart")  # noqa: F821
