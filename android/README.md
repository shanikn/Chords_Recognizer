ChordChart for Android
======================

A standalone Android app that runs ChordChart's whole chord analysis on the phone (no
PC, server or Python). Work in progress, in phases; see docs/PHASE0.md for the stack,
the verified ONNX models, the plan for exact reimplementation, licenses and estimates.

    models/   chord_features.onnx, key.onnx: madmom's CNNs (CC BY-NC-SA 4.0, see PHASE0.md)
    tools/    export_models.py   rebuild the madmom networks in PyTorch and export them
              verify_models.py   compare the ONNX models with the Python pipeline
              dbn_lowmem.py      the low-memory Viterbi, checked against madmom
    docs/     PHASE0.md

    uv run --with onnx --with onnxscript python android/tools/export_models.py
    uv run python android/tools/verify_models.py
    uv run python android/tools/dbn_lowmem.py
