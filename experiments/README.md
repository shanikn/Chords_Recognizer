# Experiments

Measurements and prototypes behind decisions about the chord pipeline. Nothing here is
used by `chordchart`; the default beat tracker is still madmom. Outputs (models,
activations, reference files, results) go to `experiments/out/`, which is never
committed. Pass another folder as the first argument to put them elsewhere.

The songs are the ones in chordchart's download cache
(`%LOCALAPPDATA%\chordchart\cache\downloads`): analyse a few links first. The timing
scripts use four of them (`common.TIMING_SONGS`).

## Timing: where the time goes

    uv run python experiments/timing/measure_stages.py     # as users run it (--refresh)
    uv run python experiments/timing/measure_isolated.py   # each stage alone

2026-09-27, 4-core/8-thread laptop, seconds:

| song | length | as run: beats / chords / key / total | alone: beats / chords / key |
|---|---|---|---|
| Adele, Someone Like You | 285 | 66.8 / 73.2 / 12.9 / 91.5 | 49.3 / 34.0 / 9.5 |
| Cheek To Cheek | 352 | 77.4 / 86.3 / 19.4 / 109.3 | 52.3 / 39.3 / 12.1 |
| Für Elise | 173 | 39.1 / 40.6 / 7.8 / 51.0 | 25.6 / 17.9 / 6.3 |
| Riptide | 204 | 44.9 / 47.9 / 8.1 / 59.1 | 29.0 / 21.2 / 7.5 |

As run, beats overlaps chords then key, and they compete for the CPU. Alone, beats is
about half of the work, and within beats the RNN about 80 %.

## DBN settings

    uv run python experiments/dbn/dbn_variants.py

RNN activations once per song, then madmom's DBN at the current settings (55-205 BPM, 60
tempi) and five narrower ones. Rejected: no variant keeps the bar lines on all 11 songs,
even within 50 ms (best 8/11; Für Elise's tempo doubles under every variant), and the DBN
is only ~20 % of the beat time. Also found: the current DBN's Viterbi back-pointers take
frames x states x 4 bytes, about 2 GB for a 6-minute song in 4/4.

## Beat This! without torch

Beat This! (CPJKU, MIT code and weights) small0, exported to ONNX and run on
onnxruntime with a numpy log-mel frontend and numpy postprocessing
(`beat_this/beat_this_onnx.py`).

1. Download step, in a throwaway environment (the project never depends on beat-this,
   torch or torchaudio for this): fetches the checkpoint, exports
   `out/beat_this/beat_this_small0.onnx`, checks it against torch, and with `--refs` saves
   the original implementation's outputs for three cached songs.

       uv run --no-project --python 3.12 --with beat-this --with onnx --with onnxruntime --with "torch==2.14.0" python experiments/beat_this/export.py --refs

2. Check the prototype against the original:

       uv run python experiments/beat_this/verify.py

   ONNX vs torch within 5e-6; spectrogram within 2.4e-5 (relative); beats and downbeats
   identical to the original with both the minimal and the DBN postprocessing.

3. Speed and agreement with madmom on every cached song:

       uv run python experiments/beat_this/benchmark.py

   11 songs: 106 s vs 349 s (3.3x faster), much less memory. Against madmom (not ground
   truth): beat F 0.838, downbeat F 0.856 (+-70 ms), same meter 9/11, same bar lines 1/11
   (minimal postprocessing doesn't force regular bars; tempo-octave disagreements on
   three songs). Accuracy against annotations: `evaluate/`.
