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

The app:
    cd android && ./gradlew assembleDebug    # both ABIs (phone + emulator), debug key
    cd android && ./gradlew assembleRelease  # arm64 only, R8-shrunk, signed (below)
    -> app/build/outputs/apk/{debug,release}/

Release APK on your phone
-------------------------
GitHub builds it: Actions -> "Android release APK" runs on every push to main that touches
the app (or Run workflow by hand) and attaches ChordChart.apk to the run. Pushing a tag
such as v0.2.0 also publishes it as a GitHub release; open that page on the phone,
download the APK, and allow installing from your browser when Android asks.

Sign every build with the same key, or Android refuses to install a new one over the old
one. Make the key once (keep the file and passwords somewhere safe; losing them means
uninstalling the app, and its history, to install a build signed with a new key):
    keytool -genkeypair -v -keystore chordchart-release.jks -alias chordchart -keyalg RSA -keysize 4096 -validity 36500

For local release builds, android/keystore.properties (git-ignored; path relative to android/):
    storeFile=../../keys/chordchart-release.jks
    storePassword=...
    keyAlias=chordchart
    keyPassword=...

For GitHub, repository Settings -> Secrets and variables -> Actions, four secrets:
    CHORDCHART_KEYSTORE_BASE64    the .jks file, base64 (PowerShell:
                                  [Convert]::ToBase64String([IO.File]::ReadAllBytes("chordchart-release.jks")) | Set-Clipboard)
    CHORDCHART_KEYSTORE_PASSWORD  CHORDCHART_KEY_ALIAS  CHORDCHART_KEY_PASSWORD

Without a key the release build is signed with the machine's debug key, which works for a
first install but differs between machines (and on every GitHub run). Pull request builds
always use that throwaway key: the PR's own build scripts never get your release key.
