ChordChart for Android
======================

A standalone Android app that runs ChordChart's whole chord analysis on the phone (no
PC, server or Python). Work in progress, in phases; see docs/PHASE0.md for the stack,
the verified ONNX models, the plan for exact reimplementation, licenses and estimates.

    core/     the analysis in platform-independent C++ (also builds on the PC; no Android
              code): spectrograms, Beat This!, the DBN, the CRF, the chart
    app/src/main/assets/analysis/   ONNX models and constant tables the core loads
    tools/    Python: exports and checks against the desktop pipeline
    docs/     PHASE0.md, PHASE1.md

Setup:
    uv run python android/tools/fetch_deps.py        # pocketfft, libsoxr, ONNX Runtime -> third_party/
    uv run python android/tools/export_tables.py     # tables (+ copies the Beat This! model)
    uv run --with onnx --with onnxscript python android/tools/export_models.py   # the madmom CNNs

The core on the PC (MSVC Build Tools 2022, the Android SDK's CMake):
    android\build_host.bat
    uv run python android/tools/make_golden.py       # every Python stage, per cached song
    android\build\host\golden_test.exe android\app\src\main\assets\analysis android\golden
