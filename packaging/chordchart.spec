# PyInstaller spec for the ChordChart Windows app. Build with:
#     uv run python packaging/build.py --variant lite|full
# (build.py fetches ffmpeg and sets CHORDCHART_VARIANT). One folder (not onefile):
# starts faster and trips fewer antivirus heuristics. Both variants run ChordChart.exe;
# only the folder, zip and installer are named after the variant (variants.py).

import os
import sys

from deno import find_deno_bin
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

sys.path.insert(0, SPECPATH)  # noqa: F821 (SPECPATH is set by PyInstaller)
import variants  # noqa: E402

VARIANT = variants.current()
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))  # noqa: F821
VENDOR = os.path.join(SPECPATH, "vendor")  # noqa: F821

datas = []
datas += collect_data_files("madmom")  # the pickled models (and their LICENSE)
datas += collect_data_files("yt_dlp_ejs")  # YouTube's JavaScript challenge solver
datas += [(os.path.join(ROOT, "chordchart", "web", "index.html"), "chordchart/web")]
# The default beat tracker's model (Beat This! small0, ONNX) and its license: both builds.
for name in ("beat_this_small0.onnx", "beat_this-LICENSE.txt"):
    datas += [(os.path.join(ROOT, "chordchart", "models", name), "chordchart/models")]
licenses = os.path.join(SPECPATH, "build", VARIANT.key, "licenses")  # noqa: F821
if os.path.isdir(licenses):  # from collect_licenses.py
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
    # The app's window (pywebview on WebView2, through pythonnet). pyinstaller-hooks-contrib
    # collects pywebview's WebView2 DLLs and pythonnet's runtime.
    + ["chordchart.desktop.window", "chordchart.desktop.instance", "webview", "clr"]
)

# mutagen is GPL and yt-dlp only uses it to tag files.
excludes = ["mutagen", "pytest", "tkinter", "PyInstaller", "IPython"]

if VARIANT.notes:
    import basic_pitch

    # basic-pitch ships its model in four formats; the app runs only the ONNX one (2 MB).
    # Demucs's weights aren't bundled: they're downloaded on first use (notes/stems.py).
    models = os.path.join(os.path.dirname(basic_pitch.__file__), "saved_models", "icassp_2022")
    datas += [(os.path.join(models, "nmp.onnx"), "basic_pitch/saved_models/icassp_2022")]
    # The Sheet view's renderer (OpenSheetMusicDisplay), served locally: works offline.
    vendor = os.path.join(ROOT, "chordchart", "web", "vendor")
    datas += [(os.path.join(vendor, "opensheetmusicdisplay.min.js"), "chordchart/web/vendor")]
    # Imported lazily. Demucs loads its model class by name from the weights' metadata.
    hiddenimports += ["chordchart.notes.pipeline", "chordchart.notes.midi"]
    hiddenimports += ["chordchart.notes.stems", "chordchart.notes.transcribe"]
    hiddenimports += ["demucs.htdemucs", "demucs.hdemucs", "demucs.demucs"]
    # lameenc (LGPL, MP3 encoding) and sphn are only used by Demucs's file writing,
    # which the app doesn't use; the others are model runtimes basic-pitch can't use here.
    excludes += ["lameenc", "sphn", "tensorflow", "coremltools", "tflite_runtime", "matplotlib"]
else:
    # Chords only: leave out every package only the notes feature pulls in. The app
    # notices at startup (chordchart.notes.available) and hides the Notes controls.
    excludes += variants.notes_only_modules() + ["tensorflow", "matplotlib"]

a = Analysis(  # noqa: F821
    [os.path.join(ROOT, "chordchart", "desktop", "app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)

if VARIANT.notes:
    # torch_cpu.dll needs vcruntime140_threads.dll, which PyInstaller doesn't collect, and
    # a clean Windows doesn't have it: torch then fails to load ("WinError 126", found by
    # the Windows Sandbox test). It imports vcruntime140.dll, and PyInstaller's copy of
    # that comes from Python (older), so the whole Microsoft C++ runtime is taken from
    # this PC's installed Visual C++ Redistributable, one consistent version.
    vc_runtime = [
        "vcruntime140.dll", "vcruntime140_1.dll", "vcruntime140_threads.dll",
        "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
        "msvcp140_codecvt_ids.dll", "vcomp140.dll", "concrt140.dll",
    ]  # fmt: skip
    system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    required = os.path.join(system32, "vcruntime140_threads.dll")
    if not os.path.isfile(required):
        raise SystemExit(
            "vcruntime140_threads.dll not found: install the current Visual C++ "
            "Redistributable (winget install Microsoft.VCRedist.2015+.x64) and build again"
        )
    replace = {name.lower() for name in vc_runtime}
    a.binaries = [b for b in a.binaries if os.path.basename(b[0]).lower() not in replace]
    a.binaries += [
        (name, os.path.join(system32, name), "BINARY")
        for name in vc_runtime
        if os.path.isfile(os.path.join(system32, name))
    ]

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
coll = COLLECT(exe, a.binaries, a.datas, name=VARIANT.app_name)  # noqa: F821
