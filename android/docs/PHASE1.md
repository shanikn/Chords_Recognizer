# ChordChart for Android — Phase 1: the core on Android

The whole chord analysis now runs on Android: the phone decodes the file, a C++ core
computes beats, bars, chords and key, and the result is the desktop app's Song. Measured on
the Android Studio emulator (Android 15, x86_64, 4 cores; 3 GB RAM, because a 6 GB virtual
phone doesn't fit next to everything else on this 16 GB PC — memory is measured per
process, so this doesn't change the figures below).

## 1. What was built

- **`android/core/`: the analysis in C++, platform-independent.** No Android (or iOS) code
  in it: it builds for the PC (MSVC, `android/build_host.bat`), for Android (the app's
  CMake), and will for iOS. Stages, each a port of the Python pipeline:

  | Stage | Python reference | C++ |
  |---|---|---|
  | chord/key spectrograms | madmom `LogarithmicFilteredSpectrogram` | `spectrogram.cpp` (pocketfft, numpy's FFT) |
  | Beat This! frontend | `soxr.resample` + `beat_this.log_mel` | `resample.cpp` (the libsoxr build python-soxr uses, called the same way), `spectrogram.cpp` |
  | Beat This! model | `BeatThisModel.logits` (chunks) | `beat_this.cpp` + ONNX Runtime |
  | DBN (bars, beats) | madmom `DBNDownBeatTrackingProcessor` | `dbn.cpp`: the exact low-memory Viterbi from phase 0 |
  | chord CNN, key CNN | madmom (desktop: + fastconv) | ONNX Runtime (models from phase 0) |
  | CRF | madmom `ConditionalRandomField` | `crf.cpp` |
  | chart | `postprocess.py`, `pipeline._analyze`, `beats.py` | `chart.cpp` |
  | decoded audio -> mono 44.1 kHz | ffmpeg `-ac 1 -ar 44100` | `audio.cpp` (libsoxr HQ) |

  The constant tables (filterbanks, windows, the DBN's bar HMMs, the CRF) are exported from
  the Python pipeline's own objects (`tools/export_tables.py`), so they're identical by
  construction; the core never rebuilds them.
- **`android/app/`: the Android side.** Kotlin; the core via a thin JNI layer
  (`app/src/main/cpp/jni.cpp`, the only Android-specific C++); decoding with MediaExtractor
  + MediaCodec, fed to the core's resampler as it arrives; the models and tables shipped
  in the APK's assets. No UI yet (phase 2): a debug-only `BenchmarkActivity` runs the
  analysis for the tests.
- **Tools.** `fetch_deps.py` (pinned third-party code with SHA-256), `export_tables.py`,
  `make_golden.py` (every Python stage per song), `run_emulator.py` (all songs on the
  emulator, compared with Python), `check_formats.py` (every audio format).

## 2. Correctness

**The port, stage by stage (PC, `core/tests/golden_test.cpp`).** Each C++ stage runs on the
Python stage's own input, on all 25 cached songs:

| Stage | Result on 25 songs |
|---|---|
| chord and key spectrograms | within 1.1e-7 relative |
| resampling (libsoxr) | bit-identical |
| Beat This! log-mel | within 3e-5 relative (numpy multiplies by the filterbank with BLAS, in another order) |
| Beat This! logits, DBN activations | bit-identical |
| Viterbi paths (3/4 and 4/4, low-memory) | identical, with their log-probabilities |
| tracked beats | identical |
| chord CNN | within 1.4e-6 relative |
| CRF path, chord segments, key | identical |
| chart (bars, chords, BPM, meter) | identical |
| **whole analysis from the same PCM** | **identical to Python on 25/25 songs** |

**On Android, end to end (emulator, the original files).** The phone decodes the songs
(YouTube's WebM/Opus) with its own decoder instead of ffmpeg, so the samples differ
slightly, and a few decisions move by a frame:

| | 25 songs (81 min of audio) |
|---|---|
| chords (share of 0.1 s steps with the same chord) | **99.94%** (lowest song 99.1%) |
| bar lines within 25 ms | **99.95%**: 2232 of 2238 bar starts at exactly the same time, 6 moved by 1-3 beat frames (20-60 ms) |
| key | **25/25 the same** |
| BPM, meter | same meter everywhere; BPM within 3e-13 |
| errors | none |

**Beatles evaluation** (`evaluate/`, the same scoring and alignment, on the phone's results;
10 songs, duration-weighted):

| | Desktop (Python) | Android (emulator) |
|---|---:|---:|
| chart chords, major/minor | 0.8780 | 0.8780 |
| chart chords, root | 0.8843 | 0.8843 |
| chart chords, MIREX | 0.8556 | 0.8555 |
| chart segmentation | 0.8297 | 0.8296 |
| beat F-measure | 0.8223 | 0.8226 |
| downbeat F-measure | 0.8169 | 0.8173 |
| CMLt / AMLt | 0.8943 | 0.8945 |
| meter correct | 10/10 | 10/10 |

**Formats** (`check_formats.py`: Yesterday converted with ffmpeg; the phone's decoders vs
the Python pipeline on the same file):

| Format | Phone decoder | vs Python |
|---|---|---|
| MP3, 44.1 kHz | c2.android.mp3.decoder | identical |
| WAV, 44.1 kHz | c2.android.raw.decoder | identical |
| Ogg Vorbis, 44.1 kHz | c2.android.vorbis.decoder | identical |
| FLAC, 48 kHz (resampled on the phone) | c2.android.raw.decoder | identical |
| AAC (m4a), 44.1 kHz | c2.android.aac.decoder | chords 100%, bar lines 100%, key same |
| Ogg Opus, 48 kHz | c2.android.opus.decoder | chords 100%, bar lines 100%, key same |

So the port and the phone's resampling reproduce the desktop exactly when the decoder is
bit-exact (PCM, FLAC, MP3, Vorbis); AAC and Opus decoders differ slightly between Android
and ffmpeg, which moves an occasional beat by a frame (the 25-song run above is all Opus).

## 3. XNNPACK for Beat This!

ONNX Runtime's XNNPACK execution provider (in the Android package) was tried for Beat This!:

| 25 songs, per 4-minute song | Default CPU provider | XNNPACK |
|---|---:|---:|
| Beat This! | 15.4 s | 15.3 s |
| whole analysis | 16.8 s | 16.6 s |
| peak memory | 870-920 MB | 1557-1603 MB |
| beats and bars vs CPU | — | identical on 25/25 |

Beats and bars stay identical, so it's allowed; but on the emulator it's no faster and uses
~690 MB more. **Decision: kept as an option (`AnalyzerConfig.xnnpack`), off by default.**
The emulator runs x86 code; XNNPACK's advantage is its ARM NEON kernels, so this is the
first thing to re-measure on a real phone (a one-line switch).

## 4. Time and memory

Emulator (x86_64 on this PC's i7-1165G7, 4 virtual cores), per 4-minute song:

| | Emulator |
|---|---:|
| load models and tables (once) | 3.1 s |
| decode (MediaCodec) | 34 s (emulator overhead, see below) |
| analysis | 16.8 s |
| — Beat This! | 15.4 s |
| — chords + key (beside it) | ~3 s |
| — DBN (low-memory Viterbi, 2 meters in parallel) | 0.8 s |
| peak memory (whole app process) | 870-920 MB |

- **Decoding is slow only on the emulator:** 97% of it is Android's software Opus decoder
  exchanging a buffer per 20 ms packet (6,000-18,000 per song) with the media process; the conversion to mono 44.1
  kHz takes 0.3 s. On a phone the same decoder runs natively (expected 1-3 s); WAV decodes in
  1.8 s even here.
- **Mid-range 6 GB phone (estimate, unchanged from phase 0):** analysis ~25-35 s for a
  4-minute song, dominated by Beat This!; decoding a few seconds; peak memory ~0.9 GB.
- The debug APK is 114 MB (two ABIs, uncompressed models, native symbols); the
  release build in phase 2 will be much smaller.

## 5. Where a real phone matters now

1. **Speed**: every time above is x86. Beat This! on ARM (ONNX Runtime's NEON kernels), and
   XNNPACK there — the one open decision.
2. **Decoding speed and vendor decoders**: the emulator's decoding cost is IPC overhead;
   real phones use their vendors' codecs (Qualcomm, MediaTek, Samsung).
3. **Numerics on ARM**: float rounding differs from x86; the golden tests should run once
   on a phone (the same core, as an instrumented test).
4. **Memory**: ~0.9 GB peak per analysis; vendor memory limits and low-memory killing.
5. **Thermal throttling** on long songs.

## 6. Reproduce

    uv run python android/tools/fetch_deps.py
    uv run python android/tools/export_tables.py
    android\build_host.bat
    uv run python android/tools/make_golden.py
    android\build\host\golden_test.exe android\app\src\main\assets\analysis android\golden
    cd android && gradlew assembleDebug
    (start the emulator)  uv run python android/tools/run_emulator.py
    uv run python android/tools/check_formats.py
