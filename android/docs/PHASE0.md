# ChordChart for Android — Phase 0: feasibility and plan

Goal: the whole chord analysis (beats, bars, chords, key) on the phone, giving the same
chart as the desktop pipeline, with no PC, server or Python. Target: a typical mid-range
Android phone with 6 GB RAM (e.g. Snapdragon 7-series / Dimensity 7000-series: 4 fast
Cortex-A7x cores + 4 small ones). Development and tests run on the Android Studio
emulator (x86_64 image); what that can't show is listed at the end.

## 1. Stack: Kotlin + Jetpack Compose, analysis core in C++

**Recommendation: a native Kotlin app (Jetpack Compose UI) with the analysis in a C++
library (NDK, called through a thin JNI layer), models on ONNX Runtime.**

Why, in order of weight:

1. **The core must reproduce the Python numbers.** The pieces that decide the result
   (FFTs, filterbanks, resampling, the DBN's Viterbi, the CRF) are numeric code. In C++ we
   can use the *same* libraries the Python pipeline uses: pocketfft (numpy's FFT) and
   libsoxr (the resampler behind the Beat This! frontend), so differences stay at float
   rounding. A JavaScript or Kotlin reimplementation would need its own FFT and resampler,
   which drift further.
2. **Golden-file tests run on the PC.** A C++ core builds with CMake for Windows too, so
   the comparisons against the Python pipeline run in seconds on the PC, with no emulator;
   the same tests then run on the emulator/phone as instrumented tests.
3. **Speed and memory.** The DBN Viterbi is ~10⁹ simple operations per song, and the
   spectrograms need thousands of FFTs; C++ with tight memory control (see §4) is the
   safe choice for a 6 GB phone. ONNX Runtime has an official C/C++ and Java API and an
   Android package (`onnxruntime-android`, with ARM NEON kernels).
4. **The UI is small** (pick or share a file, progress, the chart, history). Compose does
   that with less machinery than React Native: no JS bridge, no Metro, no Expo dev-build
   toolchain, one language for the app layer, and direct access to MediaCodec, the file
   picker and share intents.
5. **It keeps iOS open.** You have an iPhone: the C++ core (and the ONNX models) compile
   for iOS unchanged; an iOS app would only need its own UI (SwiftUI) and decoder
   (AVFoundation). React Native would share the UI too, but the core would be the same
   C++ either way, and on Android RN adds a layer without making the analysis easier.

React Native (Expo dev build + a native module) is viable if you'd rather write the UI
in React; it would wrap this same C++ core. I'd switch only if a shared iOS/Android UI
becomes a goal.

Pieces: Kotlin 2.x, Compose (Material 3), minSdk 26 (Android 8), targetSdk 35; NDK +
CMake; `onnxruntime-android`; pocketfft (header-only C++); libsoxr (built with the NDK as
a shared library, see licenses); JSON results in app storage.

## 2. The models on ONNX: done and verified

| Model | File | Size | Input -> output | Verified against |
|---|---|---|---|---|
| Beat This! small0 | `chordchart/models/beat_this_small0.onnx` (existing) | 10.6 MB | log-mel (1, T≤1500, 128) -> beat, downbeat logits | original PyTorch (2026-09-27): spectrogram within 2.4e-5, beats identical |
| madmom chord features CNN | `android/models/chord_features.onnx` | 3.7 MB | log spectrogram (1, T, 113) -> features (1, T, 128) | madmom and the desktop pipeline, below |
| madmom key CNN | `android/models/key.onnx` | 2.6 MB | log spectrogram (1, T, 105) -> 24 key probabilities | same |

`android/tools/export_models.py` rebuilds both madmom networks in PyTorch from madmom's
own weights and exports them (kernels flipped: madmom convolves, PyTorch correlates). The
chord model includes madmom's zero padding, the 3-frame "superframes" and the global
average, so its output is exactly what the CRF takes.

`android/tools/verify_models.py`, on all 25 songs in the desktop app's cache (28
comparisons: every song against the desktop pipeline, 3 also against madmom's own,
unaccelerated networks):

- **decoded chords identical 28/28, key identical 28/28**;
- largest feature difference 1.4e-6 relative, key probabilities within 1.2e-6.

(On this PC the two ONNX models take 0.3-1.7 s per song together.)

## 3. Reimplementing the rest exactly, with golden-file tests

What runs outside the models, and how each piece is ported (C++ unless noted):

| Stage | Python reference | Port | Must match |
|---|---|---|---|
| Decode to 44.1 kHz mono int16 | ffmpeg `-ac 1 -ar 44100 pcm_s16le` (`fetch._ffmpeg_decode`) | MediaCodec (Kotlin) -> PCM; downmix (L+R)/2 and resample to 44.1 kHz with soxr when the source isn't 44.1 kHz | samples within decoder rounding (see below) |
| Chord spectrogram, 10 fps | madmom `FramedSignal(8192, fps=10)` -> `STFT` (Hann / 32767) -> `LogarithmicFilteredSpectrogram(24 bands/oct, 60-2600 Hz, unique filters)` = log10(1 + x) | frames centred on `i*hop` with zero padding, pocketfft rfft, the same triangular log filterbank built with madmom's formulas | 1e-5 relative |
| Key spectrogram, 5 fps | same, 65-2100 Hz | same code, other parameters | 1e-5 relative |
| Beat This! frontend | `soxr` 44.1 -> 22.05 kHz, then `beat_this.log_mel` (1024 FFT, hop 441, reflect padding, Slaney mel, log1p(1000x)) | libsoxr (same settings: HQ), pocketfft, same mel filterbank | 1e-5 relative (as the existing numpy port) |
| Chunks for the model | `BeatThisModel.logits`: 1500-frame chunks, 6-frame borders, first chunk wins | same indexing | identical logits placement |
| DBN (bars and beats) | madmom `DBNDownBeatTrackingProcessor` (bar-pointer HMM, 55-215 bpm, λ=300, 3/4 and 4/4, observation model, `correct` peak refinement) | state space, transition and observation models built with madmom's formulas; low-memory Viterbi (§4) | identical beats and downbeats |
| CRF chord decoding | madmom `CRFChordRecognitionProcessor` (linear-chain CRF Viterbi, 25 classes) | port of `ConditionalRandomField.process` + label mapping | identical segments |
| Key label | `key_prediction_to_label` | argmax -> name | identical |
| Chart | `chordchart/postprocess.py` (`beat_sync`, `group_bars`, `split_points`, `_quantize_bar`) and the chart-range logic in `pipeline._analyze` | port (Kotlin or C++), rules unchanged | identical `Song` JSON |

**Golden files.** `android/tools/make_golden.py` (Phase 1) runs the Python pipeline on a
song and saves every intermediate: the decoded PCM, the three spectrograms, the logits,
the DBN activations, the DBN paths and beats, the chord features, the CRF segments, the
key probabilities, the beat-synced labels and the final `Song` JSON. Each C++ stage is
then tested **on the Python stage's input**, so a small upstream difference (e.g. a
decoder's rounding) can't hide or cause a failure downstream. Tolerances: float stages
1e-5 relative; decision stages (DBN path, CRF segments, key, bars) must be identical.

- Real songs are copyrighted, so their golden files are generated locally from your
  cache into `android/golden/` (git-ignored). Committed fixtures: the synthetic
  progressions the desktop tests already generate (click track, early-strum track).
- End to end (Phase 1): the app's own decoding + pipeline on the 25 cached songs vs
  Python, reporting agreement % for chords (per beat) and bar lines, and the Beatles
  evaluation (`evaluate/`) within a small tolerance.

**Where exact equality is not possible:** the audio decoder. Android's AAC/Opus/MP3
decoders are not ffmpeg's, so decoded samples differ by rounding, occasionally more
(decoder delay/priming handling). That's why Phase 1 reports agreement rather than
promising identity end to end; everything after decoding is tested for exactness.

## 4. DBN memory on long songs: solved, identical results

madmom's Viterbi stores a 4-byte back-pointer per frame and state. At Beat This!'s 50 fps
the 4/4 bar model has 5796 states (3/4: 4347): **265 MB (+199 MB) for a 4-minute song**,
389 MB (+292 MB) for a 6-minute one. Too much next to everything else on a phone.

Two exact changes (prototype: `android/tools/dbn_lowmem.py`):

1. A state has at most 12 predecessors, so each back-pointer is a 1-byte index into the
   state's predecessor list (4x less).
2. Checkpoints: going forward, only every K-th frame's scores are kept (K ≈ √frames);
   backtracking recomputes one K-frame segment at a time from its checkpoint.

The prototype uses madmom's arithmetic in madmom's order (previous + transition +
density, float64; a later predecessor wins only if strictly greater), so the result is
not "close", it is the same:

| Song | Meter | madmom back-pointers | Low-memory | Path and log-probability |
|---|---|---:|---:|---|
| Yesterday (2:06) | 3/4 | 104 MB | 3.0 MB | identical |
| Yesterday (2:06) | 4/4 | 139 MB | 3.9 MB | identical |
| Cheek (5:52) | 3/4 | 292 MB | 5.0 MB | identical |
| Cheek (5:52) | 4/4 | 389 MB | 6.6 MB | identical |

Cost: the forward pass runs twice. In C++ that's ~2x madmom's Cython (about 2 s per
meter for a 6-minute song on this PC, ~4 s on the target phone; the two meters can run
on two cores).

**Beat This! is the real memory peak.** onnxruntime by default keeps an arena sized for
the largest chunk: +1.3 GB while running. Without the arena: **+633 MB, same logits,
~16% slower** (10.1 -> 11.7 s on this PC for 4 minutes of audio); turning off memory
planning as well only saves 54 MB more and doubles the time. The app will run Beat This!
without the arena. (The chunk size, 1500 frames, is what the model was trained on;
shrinking it would change the beats, so it stays.)

## 5. Licenses

| Component | License | What it means for the app |
|---|---|---|
| madmom model files (chord CNN, CRF, key CNN) — converted to ONNX | **CC BY-NC-SA 4.0** | Free app only: no ads, no paid version, no in-app purchases. Attribution in the app ("madmom, CPJKU, CC BY-NC-SA 4.0"). The ONNX conversions are adaptations: they must stay CC BY-NC-SA, and anyone may reuse them on those terms. |
| madmom code (ported: spectrogram frontends, DBN, CRF) | BSD-2-Clause (code) | Keep the copyright notice in the ported sources and the app's licenses screen. |
| Beat This! code and model | MIT | Notice. |
| ONNX Runtime | MIT | Notice. |
| pocketfft | BSD-3-Clause | Notice. |
| libsoxr | **LGPL-2.1** | Fine in a free or closed app when linked dynamically (it's its own `.so`); include the license and its source (or a written offer). Not GPL. |
| Kotlin, Jetpack Compose, AndroidX | Apache-2.0 | Notice. |
| Phase 3, YouTube via NewPipe Extractor | **GPL-3.0** | The whole app would have to be distributed under GPL-3.0. Separately, Google Play forbids apps that download YouTube content, so it would be a sideload-only build. Details before that phase. |
| Phase 3, basic-pitch | Apache-2.0 (code and model) | Notice. |
| Phase 3, Demucs (htdemucs) | MIT | Notice (feasibility on a phone is the real question). |
| Phase 3, OpenSheetMusicDisplay | BSD-3-Clause | Notice. |

Nothing GPL in Phases 0-2. No FFmpeg in the app (MediaCodec decodes), which avoids its
LGPL/GPL build questions.

## 6. Time and memory estimate: a 4-minute song on a mid-range 6 GB phone

Basis: this PC (Intel i7-1165G7, 4 cores) measured per stage, scaled to 4 minutes, and a
mid-range phone's 4 fast cores taken as roughly 2-2.5x slower for this kind of code
(ONNX Runtime's ARM kernels, C++ DSP).

| Stage | This PC (4 min) | Mid-range phone (estimate) |
|---|---:|---:|
| Decode + downmix (MediaCodec) | ~1 s (ffmpeg) | 1-3 s |
| Spectrograms + resampling (FFT, soxr) | <1 s | 1-2 s |
| Beat This! (4 threads, no arena) | 11.7 s | 23-30 s |
| DBN, low-memory, 2 meters in parallel | ~2 s | 3-5 s |
| Chord + key CNNs (ONNX) | ~0.5 s | 1-2 s |
| CRF, key, chart | <0.2 s | <0.5 s |
| **Total** (chords and key run beside Beat This!) | ~15 s | **~30-40 s** |

**Peak memory: ~0.8-1 GB** of the app's process (Beat This! +633 MB, the audio ~60 MB in
its forms, DBN ~10 MB, models ~20 MB, the app itself ~100-150 MB). A 6 GB phone gives a
foreground app that comfortably; the analysis runs in a foreground service so it
survives the screen turning off.

Repeat analysis of the same song: instant (results cached by audio hash, as on desktop).

## 7. Emulator vs a real phone: when a real device matters

The emulator (x86_64 image) runs on this PC's CPU, so it is good for correctness,
functionality and UI, but these need a real Android phone:

1. **Timing.** Emulator numbers reflect the PC, not a phone: the estimates above stay
   estimates until measured on ARM. Also thermal throttling on long songs.
2. **Numerics on ARM.** ONNX Runtime uses different kernels on ARM (NEON) than on x86;
   float rounding differs slightly. Decision stages are robust to 1e-6, but the golden
   tests should run once on a real phone to confirm.
3. **Audio decoders.** MediaCodec implementations are vendor-specific (Qualcomm,
   MediaTek, Samsung): format support (e.g. Opus/Vorbis in odd containers), output
   sample formats and encoder-delay handling differ. The emulator only has Google's
   software decoders.
4. **Memory limits.** Low-memory killing and per-app limits depend on the vendor's
   Android build; a 0.8-1 GB peak should be checked on a real 6 GB phone.
5. **File picking and sharing** from other apps (WhatsApp, Files, Drive, a music player)
   behave differently across vendors.

Suggested: borrow any mid-range Android phone for one session near the end of Phase 1
(timings, ARM golden tests) and once at the end of Phase 2 (decoders, sharing, memory).

## 8. Phase 1 plan

1. `android/` Gradle project: `app` (Compose) and `core` (C++ via CMake, pocketfft,
   libsoxr, ONNX Runtime C API), plus a host build of `core` for PC tests.
2. `make_golden.py` + golden tests per stage, as above, until every stage matches.
3. MediaCodec decoding (mp3, m4a/AAC, wav, ogg/Opus/Vorbis) -> 44.1 kHz mono.
4. The end-to-end comparison on the 25 songs and the Beatles evaluation; time and peak
   memory on the emulator (and, if you can borrow one, a phone).
5. Setup needed here: an x86_64 system image for the emulator (~1.5 GB download) and the
   NDK + CMake from the SDK manager (~1.5 GB). The SDK, emulator and platform tools are
   already installed.
