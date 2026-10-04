# ChordChart for Android — Phase 2: the app

A usable offline app: choose or share a song, watch each stage's progress, read the chord
chart, find past charts again. Measured on the same emulator as phase 1 (Android 15,
x86_64, 4 cores, 3 GB RAM).

APK (debug): `android/app/build/outputs/apk/debug/app-debug.apk`, 118.5 MB. Build it with
`cd android && ./gradlew assembleDebug` after `tools/fetch_deps.py` and
`tools/prepare_assets.py` (the models and tables are generated, not in git; the build stops
with instructions if they're missing).

## 1. Decoding: 18–28 s → about 1 s per song, same audio

YouTube audio (WebM/Opus) took 20–48 s to decode in phase 1, almost all of it Android's
MediaExtractor handing each 20 ms packet across to the media process. The core now reads
the container itself (`core/src/demux.cpp`: Matroska/WebM and Ogg, Opus only) and decodes
with libopus in-process (`core/src/decode.cpp`), built and driven exactly like Android's
own decoder (Codec2 `C2SoftOpusDec`: fixed-point libopus 1.5, multistream, header gain,
codec delay dropped the same way). The PCM is bit-identical to MediaCodec's (SHA-256, all
songs), so the analysis can't change.

**MP3 and AAC (M4A, MP4 video)**, the most common formats on phones, now go the same way:
the core reads the file (`core/src/demux_mpeg.cpp`) and decodes it with Android 15's own
software decoders, built into the core from AOSP (android-15.0.0_r1) — the PacketVideo MP3
decoder behind `c2.android.mp3.decoder` and the Fraunhofer FDK AAC decoder behind
`c2.android.aac.decoder` (`core/src/decode_mpeg.cpp`). minimp3 or dr_mp3 would round
differently and change the charts slightly; these don't. To make the audio identical, the
core mirrors, line by line, everything between the file and the app on Android:

- which packets the extractor hands over (MP3Extractor: ID3v2 skipping, the first frame
  needs three valid successors, the Xing/Info/VBRI frame skipped, resync rules, where the
  stream ends; MPEG4Extractor: the first audio track, the sample table, the ASC's rate);
- how the Codec2 wrappers drive the decoders (C2SoftMp3Dec: the 529-sample decoder delay,
  silence for frames without main data, 529 zero samples at the end; C2SoftAacDec: raw
  config, the DRC settings, output-delay trimming and flushing, silence for bad frames);
- the gapless trimming MediaCodec applies afterwards (SkipCutBuffer) with the encoder
  delay/padding the extractor finds (LAME's tag, an MP4 edit list, iTunSMPB). This was the
  one difference in the first test (1,024–1,524 samples at the ends) and is now mirrored.

Where the core can't be sure of matching (an MP3 with an iTunSMPB tag, the output format
changing mid-stream, an MP4 audio track that isn't AAC-LC with a plain channel
configuration, fragmented MP4, decoder errors), the file goes to MediaExtractor + MediaCodec
exactly as before. FLAC, WAV, Vorbis and other formats also still use MediaCodec (WAV is fast
already).

Licences: the MP3 decoder is Apache-2.0; FDK AAC has Fraunhofer's own licence (free
redistribution with its full text and the source available; **no patent licence**), which
fits this non-commercial app. Both full texts ship in the APK and show on the About screen.

The app hands the core the file descriptor it was given, not a path: files picked from
shared storage or shared by other apps may be read only through that descriptor.

## 2. Results unchanged

- All 25 songs on the emulator with the phase 2 build (`tools/run_emulator.py --modes cpu
  --out phase2`): bars, chords, beats, key, BPM, meter, warnings and duration identical to
  phase 1's results, 25/25 (so still 99.94% chords / 99.95% bar lines vs Python, key 25/25).
- The core on the PC (`golden_test`): 25/25 identical to Python end to end after the
  progress/cancel changes.
- XNNPACK stays off by default (`ChordAnalyzer(xnnpack = false)`).

- **MP3/AAC in-process decoding is bit-identical to Android's own** (`tools/check_decoders.py`,
  PCM SHA-256): 36/36 test files — 3 songs × 12 encodings (LAME CBR with an Info tag and
  cover art, LAME VBR with a Xing tag, mono, 48 kHz 320k with an ID3v1 tag, MPEG-2 22 kHz,
  Shine, Media Foundation MP3; ffmpeg AAC at 44.1/48 kHz, mono, moov first, Media
  Foundation AAC, an MP4 video with AAC) — and 19/19 full songs converted to MP3 and M4A.
  Identical PCM means identical charts. Not covered: HE-AAC (no encoder for it here; the
  code follows the same path, but untested), iTunes-made files with iTunSMPB (they fall
  back).

## 3. Timings (emulator, 25 songs, mean 3:14 of audio)

| per song | phase 1 | phase 2 |
|---|---|---|
| decode (WebM/Opus) | 27.8 s mean, 47.7 s max | **0.9 s** mean, 1.3 s max |
| analysis | 13.0 s mean | 11.3 s mean, 23.8 s max (a 5:52 song) |
| **total, file to chart** | ~40 s | **12.2 s mean** (median 10.6 s, max 24.9 s) |
| peak memory (VmHWM) | 920 MB | 912 MB |

**MP3 and M4A** (10 of the same songs each, converted with LAME 192k / AAC 192k, mean 3:25):

| per song | before (MediaExtractor + MediaCodec) | now (in-process) |
|---|---|---|
| decode, MP3 | 18.4 s median | **0.9 s** median, 2.2 s max |
| decode, M4A | 18.0 s median | **1.8 s** median (one 14.5 s outlier while the PC was swapping) |
| **file to chart**, MP3 | ~30 s | **11.7 s median**, 13.1 s mean, 26 s max |
| **file to chart**, M4A | ~30 s | **14.4 s median**, 15.5 s mean, 26 s max |

The PC was short of memory during this run (under 1 GB free at the end), which slowed the
emulator's analysis by up to 5×; the file-to-chart figures therefore add the decode times
measured here to the analysis times of the same songs from the clean run above (the
analysis doesn't depend on the source format). Android's own decoder even failed once
("codec released") under that memory pressure; the in-process path didn't.

The beat model (Beat This!) is still most of the analysis (10.3 s mean). The first
analysis after the app starts also waits for the models to load (started in the
background at launch). A second open of the same file is instant: the chart comes from
the history (keyed by a hash of the file's bytes).

## 4. The app

- **Home** (`screenshots/home-empty.png`, `home-history.png`): "Upload a song" (the system
  file picker, audio and video), recent charts with key/BPM/length/date, delete with
  confirmation.
- **Share / open with**: `ACTION_SEND` and `ACTION_VIEW` for `audio/*` and `video/*`
  (`AndroidManifest.xml`). Tested for real: an MP3 in Downloads, long-pressed in the Files
  app, Share → ChordChart in the share sheet → analysed → chart
  (`share-1-selected.png`, `share-2-sheet.png`, `share-3-chart.png`).
- **Progress** (`analyzing-1.png`, `analyzing-2.png`): six stages (reading the audio,
  beats, chords, key, bars, chart) each with its own state and percentage, an overall bar,
  an elapsed-time clock, Cancel; the screen stays on. Stages run in parallel in the core
  (chords and key alongside beats), so several move at once. The core reports progress
  per stage (the beat model per chunk) into a session the UI polls every 100 ms; Cancel
  stops the core at its next checkpoint. Tested: Cancel during the beat model returns to
  Home at once, the app's CPU drops to ~4% within 2 s, nothing is saved to history
  (`cancel-before.png`).
- **Chart** (`chart.png`): key, tempo, meter, length, warnings; the bars in a grid, 4 per
  row upright and more when turned, each chord given room in proportion to its beats;
  no-chord shown as "N.C."; bar numbers as the analysis gives them (a pickup bar is 0).
- **About**: offline statement and the third-party notices (`assets/NOTICES.txt`),
  including madmom's CC BY-NC-SA model licence (non-commercial use only), and the full
  licence texts of the MP3 and AAC decoders.
- **Offline**: no INTERNET permission. History is JSON in the app's private storage.

## 5. Not verified

- HE-AAC and iTunes-made M4A/MP3 (iTunSMPB): no test files here. HE-AAC runs the same
  mirrored code; iTunSMPB MP3s fall back to MediaCodec (identical, but slow).
- Real-device checks that matter: the beat model's speed on ARM (and whether XNNPACK helps
  there), thermal throttling over a long song, memory on a 6 GB phone with other apps open.
  Also: phones whose makers replace Android's software MP3/AAC decoders with their own
  (some do, for power): there the old MediaCodec path could give slightly different audio
  per phone; the in-process decoders give the same audio on every phone, matching stock
  Android's.
