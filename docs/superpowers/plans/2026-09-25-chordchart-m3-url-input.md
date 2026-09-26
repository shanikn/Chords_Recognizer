# ChordChart Milestone 3: URL Input — Implementation Plan

**Status: completed 2026-09-26** (all tasks, on branch feat/m1-m2).

> **For agentic workers:** execute inline with superpowers:executing-plans. Stop after each
> task for review; commit and push after each commit.

**Goal:** `chordchart <url>` works exactly like `chordchart <file>`. Downloads are cached,
link errors are one line, and `--start/--end` analyse only part of a song.

**Architecture:** A new `chordchart/download.py` wraps yt-dlp: it resolves a link to a
cached audio file, and turns yt-dlp's exceptions into our one-line errors. `fetch.py` gets
`resolve_source()` (link or path → local file + title) and section support in
`decode_to_wav`. The pipeline shifts all times by the section start, so chord and bar
times always refer to the original audio. Nothing downstream of the WAV changes.

**Tech stack:** `yt-dlp[default,deno]` (Python API), ffmpeg `-ss/-to`, pytest with a new
`network` marker.

## Findings this plan is based on (probed 2026-09-25, yt-dlp 2026.08.19)

| Probe | Result |
|---|---|
| Public video, no JS runtime available | Downloads, but warns: YouTube without a JS runtime is deprecated, formats may be missing |
| `yt-dlp[default,deno]`, no deno/node on PATH | Downloads cleanly (the `deno` extra ships the runtime as a Python package) |
| Nonexistent/removed video | `DownloadError` wrapping `ExtractorError(expected=True)`, "This video is unavailable" |
| Non-video page (`https://example.com/`) | `UnsupportedError` (after ~9 s: the generic extractor fetches the page first) |
| Malformed `watch?v=` (no id) | **No error**: returns YouTube's "recommended" feed as a playlist |
| No network (proxy to a closed port) | `DownloadError` wrapping `yt_dlp.networking.exceptions.TransportError` |
| Offline id from URL | Works for single-video links, but `watch?v=X&list=Y` matches the playlist first, so it isn't used as the cache key |

## Decisions

1. **Dependency:** `yt-dlp[default,deno]>=2026.8.19`. The `deno` extra removes any
   dependence on a system JS runtime. `uv.lock` pins exact versions. Upgrading when
   YouTube breaks: `uv lock --upgrade-package yt-dlp && uv sync`.
2. **What counts as a link:** an argument whose scheme is `http` or `https`. Anything else
   is a path. A missing path that looks like a domain (`youtube.com/...`, `youtu.be/...`)
   fails with a hint to add `https://`, instead of "file not found".
3. **Cache location:** `%LOCALAPPDATA%\chordchart\cache\downloads` (on other platforms
   `~/.cache/chordchart/downloads`), overridable with the `CHORDCHART_CACHE_DIR`
   environment variable. This changes the spec's `.cache/` in the project, because the
   CLI can run from any directory. **Spec §3 and §4 get updated.**
4. **Cache key and layout:** files are named `<extractor>-<id>.<ext>` with a sidecar
   `<extractor>-<id>.json` (title, duration, webpage URL). `index.json` maps each
   *exact link string* to its key.
   - The same link again means an index hit: **no network at all**, so it also works
     offline.
   - A different link to the same video (`youtu.be/...` vs `watch?v=...`) means one
     metadata request (no download), and the existing file is reused.
   - `--refresh` forces a new download.
   - yt-dlp's partial files (`.part`, `.ytdl`) never count as hits.
5. **What gets rejected before download:**
   - playlists and feeds (`noplaylist` is set, and any playlist-type result is refused);
   - live streams;
   - videos longer than `--max-duration`. Downloads are checked against the *full* video
     length, so a long concert needs `--max-duration` raised even when `--start/--end`
     picks a section.
6. **Sections:** `--start` and `--end` accept `SS`, `MM:SS` or `H:MM:SS`, with optional
   decimals.
   - They're applied by ffmpeg while decoding (`-ss`/`-to`), and they work the same for
     files and links. A link is always downloaded whole once, so any section after that
     is free.
   - All times in the `Song` (beats, chords, bars) are **absolute**, relative to the
     original audio, so the JSON lines up with the song and with M4 annotations of full
     songs.
   - The chart header shows `Section: 1:05-2:10`.
   - A start past the end of the audio gets its own clear error. A section shorter than
     5 s gets the existing minimum-duration error.
7. **Errors:** all one line, exit code 2, never yt-dlp's own `ERROR: [youtube] <id>:`
   prefix:

   | Situation | Error | Message |
   |---|---|---|
   | Unsupported site, malformed link, playlist or feed | `InvalidLinkError` | `not a single-video link: <url> (<reason>)` |
   | Private, removed, age-restricted, geo-blocked, sign-in required | `VideoUnavailableError` | `video unavailable: <yt-dlp's reason>` |
   | No connection, DNS failure, timeout (`TransportError` anywhere in the cause chain) | `NetworkError` | `network error: could not reach <host>; check your internet connection` |
   | Anything else from yt-dlp | `DownloadFailedError` | `download failed: <reason>. YouTube changes often; try: uv lock --upgrade-package yt-dlp && uv sync` |

   yt-dlp's own console output is silenced, and it gets a logger that forwards to
   Python `logging`. Status goes to stderr as one line: `downloading: <title> (3:45)` or
   `using cached download: <title>`. stdout only ever carries the chart.
8. **Not in M3:** caching the analysis results (beats and chords per audio hash), which
   the spec listed with M3. You didn't ask for it, and nothing needs it yet. It moves to
   M5, where the eval sweeps would benefit. Also not in M3: downloading only a section
   (yt-dlp `download_ranges`), which would complicate the cache.

## Tasks

### Task 1: Sections in decoding

- `fetch.parse_time(text) -> float`: `"75"`, `"1:15"`, `"1:02:03"`, `"1:15.5"`. It raises
  `ValueError` on `"abc"`, `"1:75"` and negative values. The CLI uses it as an argparse
  `type`, so a bad value is a normal usage error.
- `decode_to_wav(src, dst, max_duration=..., start=0.0, end=None) -> float` puts
  `-ss`/`-to` before `-i`. If the result is empty and `start > 0`, it raises
  `AudioRejectedError("--start 5:00 is past the end of the audio")`.
- **Fast tests** (ffmpeg lavfi, no models):
  - `parse_time` accepts the valid forms and rejects the invalid ones;
  - a 20 s tone with `start=5, end=12` gives 7.0 ± 0.1 s;
  - `start` alone reads to the end;
  - a start past the end gives the clear error;
  - `end <= start` is rejected;
  - a 3 s section gives the minimum-duration error.

### Task 2: Absolute times through the pipeline

- `analyze(source, *, start=0.0, end=None, ...)` adds `start` to every beat and segment
  time before post-processing.
- `Song` gains `section_start: float = 0.0` and `section_end: float | None = None`.
  `duration` stays the analysed length.
- The text renderer adds `Section: 1:05-2:10` to the header when a section is set. The
  existing golden test is unchanged (no section), plus one new golden test with a section.
- **Slow test:** the click track with `start=4.0` gives downbeats at absolute multiples
  of 2 s, all ≥ 4.

### Task 3: `download.py`: yt-dlp, cache, errors

- `download(url, *, cache_dir=None, max_duration, refresh=False, ydl_class=yt_dlp.YoutubeDL) -> Downloaded(path, title, duration, webpage_url)`.
  - `ydl_class` is injectable so the fast tests use a fake. There's no production-only
    knob for tests beyond that.
  - It implements the cache flow from decision 4 and the rejections from decision 5.
- `classify(err: yt_dlp.utils.DownloadError, url) -> ChordChartError` implements the
  table in decision 7. The core of it:
  ```python
  def classify(err, url):
      cause = err.exc_info[1] if err.exc_info else None
      chain = _cause_chain(cause)                      # follows .cause / __cause__
      if any(isinstance(c, TransportError) for c in chain):
          return NetworkError(f"network error: could not reach {urlparse(url).hostname}; "
                              "check your internet connection")
      if isinstance(cause, UnsupportedError):
          return InvalidLinkError(f"not a single-video link: {url} (unsupported site)")
      if isinstance(cause, ExtractorError) and cause.expected:
          return VideoUnavailableError(f"video unavailable: {_reason(err)}")
      return DownloadFailedError(f"download failed: {_reason(err)}. YouTube changes often; "
                                 "try: uv lock --upgrade-package yt-dlp && uv sync")
  ```
  `_reason` strips `ERROR: `, `[extractor] <id>: ` and yt-dlp's "caused by" tail, and
  collapses the text to one line.
- **Fast tests** with a fake `YoutubeDL` (it records its calls and writes a small file
  where yt-dlp would):
  - the first call downloads and writes the index and sidecar;
  - the same link again makes **no** fake calls at all;
  - another link to the same id makes a metadata call only, with no download;
  - `refresh=True` downloads again;
  - a leftover `.part` file is not a hit;
  - playlist, live and too-long results are rejected with the right error types;
  - `CHORDCHART_CACHE_DIR` is honoured.
- **Fast tests** for `classify`, using *real* yt-dlp exception classes wrapped in
  `DownloadError` exactly as the probe showed:
  - each row of the table gives the right type;
  - every message is one line, has no `ERROR:` prefix, and names the reason.
- **`network` tests** (excluded by default: `addopts` gains `and not network`; run with
  `uv run pytest -m network`):
  - download "Me at the zoo" (`jNQXAC9IVRw`, 19 s) into a temp cache;
  - a second call with `YoutubeDL.extract_info` patched to raise proves it's served from
    the cache;
  - a nonexistent id gives `VideoUnavailableError`.

### Task 4: CLI: links, `--start/--end`, `--refresh`

- `fetch.resolve_source(arg, *, max_duration, refresh) -> ResolvedSource(path, title, source)`:
  - a link goes through `download()`, and the title comes from the video;
  - a path is used as-is, and the title is the file name;
  - the domain-without-scheme hint from decision 2.
- `analyze()` calls `resolve_source` first.
- New CLI options:
  - `--start`/`--end` (argparse `type=parse_time`, and `end <= start` is a usage error);
  - `--refresh`.
- **Fast tests** (download patched to return a local generated file):
  - `chordchart <url>` and `chordchart <that file>` print **identical charts** apart from
    the title line;
  - the options reach `analyze`;
  - each link error is exactly one `error:` line on stderr, with exit code 2;
  - status lines go to stderr, never stdout.
- **`network` test:** the real `chordchart https://www.youtube.com/watch?v=jNQXAC9IVRw`
  exits 0 and prints a header with the video title. Chords aren't asserted: it's a
  talking video.
- Spec updates: §3 (`download.py`, cache location), §4 step 1, the §8 error table, the
  M3 done criteria (adds `--start/--end`), and the analysis cache moved to M5.

### Task 5: `chordchart cache` (added at approval)

- `chordchart cache info` prints the cache location, the number of files and the total
  size. `chordchart cache clear` deletes the downloads and the index, then prints what it
  freed. `clear` only touches the cache directory, and it refuses (with an error) if
  that directory doesn't look like ours, i.e. has no `index.json` and has files that
  aren't `<extractor>-<id>.*`. That guards against a mistyped `CHORDCHART_CACHE_DIR`.
- Dispatch: when the first argument is `cache`, the `cache` subcommand parser runs.
  Otherwise the existing `chordchart <source>` parser runs. A file literally named
  `cache` can be passed as `./cache`.
- `chordchart --help` ends with the resolved cache location and the env var that
  overrides it. The README documents both commands.
- The too-long error names the option explicitly (`video is 1:32:10 long, over the
  15 min limit; raise --max-duration to download it`), and a test asserts that it
  contains `--max-duration`. The same goes for the local-file too-long error, which
  already does.
- **Fast tests:** `info` on an empty and a populated temp cache; `clear` removes the
  files and the index; `clear` refuses a foreign directory; `--help` contains the cache
  path.

### Task 6: Whole-bar expansion for sections (decided after task 3)

A `--start`/`--end` that falls mid-bar currently produces a partial first/last bar that
looks like a pickup. Instead:
- Decode `PAD = 5 s` extra on each side (clamped to the file). That's at least one bar at
  the DBN's slowest tempo (55 BPM), and it gives the models context at the edges.
- Keep beats from the last downbeat ≤ `start` to the first downbeat ≥ `end`. The chart
  covers whole bars.
- `Song.section_start/end` hold the *actual* bar-aligned range. New
  `requested_start/end` fields keep what the user typed. The header shows
  `Section: 0:04-0:16 (requested 0:05-0:15)` when the two differ.
- A pickup bar (index 0) only happens when the chart starts at 0:00 of the source.
- **Slow tests:** click track `--start 5 --end 15` gives bars from 4 s to 16 s, all with
  4 beats, no bar 0, and a header showing both ranges. With `start=0`, the pickup
  behaviour is unchanged.

### Then: finish M2 task 9 with your link

Run `uv run chordchart <your link> [--start ... --end ...]` through the real command, and
compare with your chords. The downloaded file goes to the cache outside the repo, so
nothing under `evaluate/` is created. If we also save a copy there for M4, I'll run
`git check-ignore` on it first.
