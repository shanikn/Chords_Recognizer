# Publishing Chord Chart on Google Play

The Play build is the `play` flavor (`./gradlew bundlePlayRelease`). It is the sideload
app without YouTube and Spotify links: Google Play doesn't allow apps that download
YouTube's audio, so the Play build has no NewPipe Extractor, no link field, and no
internet permission. Songs come from the phone (Upload a song, or share/open a file).
The sideload APK (`full` flavor, GitHub releases) keeps the links.

Files here:

- `privacy-policy.md`: the privacy policy. Its public address, for the Play Console:
  https://github.com/shanikn/Chords_Recognizer/blob/main/android/docs/play/privacy-policy.md
- `icon-512.png`: the store icon (512 x 512).
- `feature-graphic.png`: the feature graphic (1024 x 500).
  Both are made by `python3 android/tools/make_icon.py`.

## Steps only you can do

1. **Signing key in GitHub.** If you haven't yet: make the release key and add the four
   `CHORDCHART_*` secrets to the `release` environment (android/README.md). It becomes
   your Play *upload key*. Keep the .jks file and passwords backed up.
2. **Play Console account.** Sign up at https://play.google.com/console as a
   *personal* developer, pay the one-time US$25 fee, and verify your identity (ID
   document) and your Android phone (via the Play Console app). Verification can take
   a few days.
3. **Create the app.** Name "Chord Chart", default language English, App (not game),
   Free. Accept the declarations.
4. **Get a bundle.** GitHub, Actions, "Android release APK and Play bundle", Run
   workflow on main. Download the `ChordChart-play-aab` artifact (a zip containing
   `ChordChart-play.aab`). Each run has a higher version code (the run number), which
   Play requires for every upload.
5. **Play App Signing.** On the first upload, let Google generate and keep the *app
   signing key* (the default). Your key only signs uploads; if you lose it, Google can
   reset it. Consequence: the Play app and the sideload APK are signed differently, so
   one can't be installed over the other; uninstall the sideload app before installing
   from Play (its saved charts go with it).
6. **Fill in "Set up your app"** with the answers below (privacy policy, app access,
   ads, content rating, target audience, data safety, government apps, financial
   features, health) and the store listing (text below, icon, feature graphic, and
   2 to 8 phone screenshots).
7. **Closed test (required for new personal accounts).** Testing, Closed testing:
   create a track, upload the .aab, add at least **12 testers** (their Google account
   emails, or a Google Group), and share the opt-in link with them. They must stay
   opted in for **14 days in a row**. Friends who install it and open it a few times are
   enough; feedback helps the production review.
8. **Apply for production** (Dashboard, after the 14 days), answer the short
   questionnaire about the test, then create a production release with the same .aab
   (or a newer one). Review usually takes a few days.

## Store listing

**App name** (30 characters max): `Chord Chart`

**Short description** (80 max):

    Chords, beats, bars and key of any song, worked out offline on your phone.

**Full description** (4000 max):

    Chord Chart listens to a song on your phone and writes out its chord chart: the
    chords bar by bar, the beats, the time signature, the tempo and the key.

    Pick an audio or video file from your phone, or share one to Chord Chart from
    another app. A few moments later you get a clean chart to play along with.

    • Chords for every bar, with chord changes inside a bar
    • Key, tempo (BPM), meter and length
    • Works completely offline: songs never leave your phone, and the app has no
      internet access at all
    • No account, no ads, no tracking
    • Your charts are saved on the phone so you can open them again
    • Reads MP3, AAC/M4A, WAV, FLAC, Ogg and Opus, and the audio of video files
    • Light and dark themes

    The analysis runs neural networks from music research (madmom and Beat This!,
    both from the Institute of Computational Perception at JKU Linz) directly on
    your phone. Results are a starting point for playing along, not a transcription:
    complex jazz harmony and very dense mixes can trip it up.

    Chord Chart is free and open source (GNU GPL 3.0):
    https://github.com/shanikn/Chords_Recognizer

**Category:** Music & Audio. **Tags:** music, chords (pick what Play offers).
**Contact email:** required, shown publicly; use one you're fine publishing.

**Screenshots:** 2 to 8 phone screenshots, each side 320 to 3840 px, the long side at
most twice the short side. A 1080 x 2400 phone screenshot is too tall: crop it to
1080 x 2160 (cut the status and navigation bars). Take them from the Play build with
real song titles (home with recent charts, a chart, the analysing screen, dark mode).
The ones in docs/screenshots are from an older version.

## App content answers

- **Privacy policy:** the URL above.
- **App access:** All functionality is available without special access.
- **Ads:** No, the app does not contain ads.
- **Content rating** (IARC questionnaire): category *Utility, Productivity,
  Communication, or Other*; answer No to violence, sexuality, language, controlled
  substances, gambling, user interaction/sharing, location sharing, digital purchases.
  Expected rating: Everyone / PEGI 3.
- **Target audience:** 13 and over (don't include under-13 age groups: that brings in
  the Families policy, with extra requirements). The app isn't designed for children.
- **Data safety:**
  - Does your app collect or share any of the required user data types? **No.**
  - (So the encryption and deletion questions don't come up.) The listing will say
    "No data collected" and "No data shared with third parties".
  - Why that's accurate: the Play build has no internet permission, so nothing can
    leave the phone; charts are stored only on the device, and Play's definition of
    "collect" covers data sent off the device.
- **Government apps / Financial features / Health:** No / none / none.
- **News app:** No.

## Keep in mind

- **Keep it free, with no ads or in-app purchases.** madmom's chord and key models are
  licensed CC BY-NC-SA 4.0 (non-commercial), so the app can't be sold or monetised
  without replacing them.
- **Target API level.** Play requires new apps and updates to target a recent Android
  version (each August the minimum goes up). The app targets API 36 (Android 16); bump
  `targetSdk` in app/build.gradle.kts when Play announces the next one.
- **16 KB memory pages.** Play requires native libraries to work on 16 KB-page phones;
  the GitHub Action checks every library in the bundle (tools/check_page_size.py).
- **arm64 only.** The bundle has arm64-v8a libraries only, so Play offers the app to
  64-bit ARM phones (nearly all phones from the last several years), not to older
  32-bit-only phones or x86 Chromebooks.
