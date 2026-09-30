# ChordChart for Android — Phase 2: the app

A usable offline app: choose or share a song, watch each stage's progress, read the chord
chart, find past charts again. Measured on the same emulator as phase 1 (Android 15,
x86_64, 4 cores, 3 GB RAM).

APK (debug): `android/app/build/outputs/apk/debug/app-debug.apk`, 116 MB. Build it with
`cd android && ./gradlew assembleDebug` after `tools/fetch_deps.py`.

## 1. Decoding: 28 s → 0.9 s per song, same audio

YouTube audio (WebM/Opus) took 20–48 s to decode in phase 1, almost all of it Android's
MediaExtractor handing each 20 ms packet across to the media process. The core now reads
the container itself (`core/src/demux.cpp`: Matroska/WebM and Ogg, Opus only) and decodes
with libopus in-process (`core/src/decode.cpp`), built and driven exactly like Android's
own decoder (Codec2 `C2SoftOpusDec`: fixed-point libopus 1.5, multistream, header gain,
codec delay dropped the same way). The PCM is bit-identical to MediaCodec's (SHA-256, all
songs), so the analysis can't change.

Other formats (MP3, AAC/M4A, FLAC, WAV, Vorbis, video files) still go through MediaCodec,
as in phase 1 (10–20 s for a 2-minute MP3/AAC on the emulator; WAV 1.8 s). On a real phone
this IPC cost is typically much lower than on the emulator; worth measuring there.

The app hands the core the file descriptor it was given, not a path: files picked from
shared storage or shared by other apps may be read only through that descriptor.

## 2. Results unchanged

- All 25 songs on the emulator with the phase 2 build (`tools/run_emulator.py --modes cpu
  --out phase2`): bars, chords, beats, key, BPM, meter, warnings and duration identical to
  phase 1's results, 25/25 (so still 99.94% chords / 99.95% bar lines vs Python, key 25/25).
- The core on the PC (`golden_test`): 25/25 identical to Python end to end after the
  progress/cancel changes.
- XNNPACK stays off by default (`ChordAnalyzer(xnnpack = false)`).

## 3. Timings (emulator, 25 songs, mean 3:14 of audio)

| per song | phase 1 | phase 2 |
|---|---|---|
| decode (WebM/Opus) | 27.8 s mean, 47.7 s max | **0.9 s** mean, 1.3 s max |
| analysis | 13.0 s mean | 11.3 s mean, 23.8 s max (a 5:52 song) |
| **total, file to chart** | ~40 s | **12.2 s mean** (median 10.6 s, max 24.9 s) |
| peak memory (VmHWM) | 920 MB | 912 MB |

The beat model (Beat This!) is still most of the analysis (10.3 s mean). The first
analysis after the app starts also waits for the models to load (started in the
background at launch). A second open of the same file is instant: the chart comes from
the history (keyed by a hash of the file's bytes).

## 4. The app

- **Home** (`screenshots/home-empty.png`, `home-history.png`): "Choose a song" (the system
  file picker, audio and video), recent charts with key/BPM/length/date, delete with
  confirmation.
- **Share / open with**: `ACTION_SEND` and `ACTION_VIEW` for `audio/*` and `video/*`
  (`AndroidManifest.xml`).
- **Progress** (`analyzing-1.png`, `analyzing-2.png`): six stages (reading the audio,
  beats, chords, key, bars, chart) each with its own state and percentage, an overall bar,
  an elapsed-time clock, Cancel; the screen stays on. Stages run in parallel in the core
  (chords and key alongside beats), so several move at once. The core reports progress
  per stage (the beat model per chunk) into a session the UI polls every 100 ms; Cancel
  stops the core at its next checkpoint.
- **Chart** (`chart.png`): key, tempo, meter, length, warnings; the bars in a grid, 4 per
  row upright and more when turned, each chord given room in proportion to its beats;
  no-chord shown as "N.C.". (The screenshot shows bar numbers one too high: fixed after
  it was taken, not re-captured — the emulator was stopped for low PC memory.)
- **About**: offline statement and the third-party notices (`assets/NOTICES.txt`),
  including madmom's CC BY-NC-SA model licence (non-commercial use only).
- **Offline**: no INTERNET permission. History is JSON in the app's private storage.

## 5. Not verified on the emulator

- Share-to-app from another app: the intent handling is in place, but the emulator's shell
  can't grant access to MediaStore files, so it was tested through the picker (the same
  cross-app permission path) and the error screen, not a real share sheet.
- Cancel mid-analysis and the corrected bar numbers: built, not exercised on screen (the
  emulator was stopped for low memory before that).
- Real-device checks that matter: MediaCodec speed for MP3/AAC, the beat model's speed on
  ARM (and whether XNNPACK helps there), thermal throttling over a long song, memory on a
  6 GB phone with other apps open.
