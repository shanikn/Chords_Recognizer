# ruff: noqa: E501  (report lines)
"""Does the in-process MP3 and AAC decoding give exactly the audio Android's gives?

    uv run python android/tools/check_decoders.py [--skip-install] [--full]

Makes MP3 and M4A/MP4 files with ffmpeg from a few cached songs, in the variants phones meet
(LAME CBR with an Info tag and cover art, LAME VBR with a Xing tag, mono, 48 kHz, MPEG-2
22 kHz, an ID3v1 tag at the end, Shine and Media Foundation encoders; AAC-LC from ffmpeg and
Media Foundation at 44.1 and 48 kHz, mono, an MP4 video, moov before or after the data),
copies them into the app on the emulator, and decodes each one three ways
(BenchmarkActivity, decode_only):

    platform    MediaExtractor + MediaCodec: how the app decoded MP3/AAC until now
    demuxer     the core's container reader + MediaCodec (isolates the reader)
    in-process  the core end to end (the new default)

The PCM (mono 44.1 kHz, what the analysis takes) must have the same SHA-256 all three ways.
Reported: the hashes, and the decode time of each path.

--full also converts every cached song to MP3 (LAME 192k) and M4A (AAC 192k) and runs the
whole analysis on them in-process (file to chart), checking the PCM against the platform
path too. Results: android/build/decoders/.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_emulator as r  # noqa: E402

OUT = r.ANDROID / "build" / "decoders"
SOURCES = ["NrgmdOz227I", "TBSpmoA8V78", "kfSQkZuIx84"]  # a few cached songs, 2-3 minutes each

VARIANTS = {  # name: (extension, ffmpeg output options)
    "mp3-lame-cbr192-cover": ("mp3", ["-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100"]),
    "mp3-lame-vbr-v2": ("mp3", ["-c:a", "libmp3lame", "-q:a", "2", "-ar", "44100"]),
    "mp3-lame-mono128": ("mp3", ["-c:a", "libmp3lame", "-b:a", "128k", "-ac", "1", "-ar", "44100"]),
    "mp3-lame-48k320-id3v1": (
        "mp3",
        ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000", "-write_id3v1", "1"],
    ),
    "mp3-lame-22k64": ("mp3", ["-c:a", "libmp3lame", "-b:a", "64k", "-ar", "22050"]),
    "mp3-shine128": ("mp3", ["-c:a", "libshine", "-b:a", "128k", "-ar", "44100"]),
    "mp3-mf192": ("mp3", ["-c:a", "mp3_mf", "-b:a", "192k", "-ar", "44100"]),
    "m4a-aac192": ("m4a", ["-c:a", "aac", "-b:a", "192k", "-ar", "44100"]),
    "m4a-aac128-48k-faststart": (
        "m4a",
        ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-movflags", "+faststart"],
    ),
    "m4a-aac96-mono": ("m4a", ["-c:a", "aac", "-b:a", "96k", "-ac", "1", "-ar", "44100"]),
    "m4a-mf160": ("m4a", ["-c:a", "aac_mf", "-b:a", "160k", "-ar", "44100"]),
    "mp4-video-aac128": ("mp4", ["-c:a", "aac", "-b:a", "128k", "-ar", "44100"]),
}
MODES = {  # name: BenchmarkActivity extras
    "platform": ["--ez", "extractor", "true"],
    "demuxer": ["--ez", "mediacodec", "true"],
    "in-process": [],
}


def ffmpeg() -> str:
    from chordchart.fetch import find_ffmpeg

    return str(find_ffmpeg())


def make(source: Path, name: str, ext: str, options: list[str], cover: Path | None) -> Path:
    target = OUT / "files" / f"{name}-{source.stem.removeprefix('youtube-')}.{ext}"
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    if name.startswith("mp4-video"):  # a small video track first, as a phone recording has
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15", "-i", str(source),
               "-map", "0:v", "-map", "1:a", "-shortest", "-c:v", "libx264", "-preset", "ultrafast"]  # fmt: skip
    elif cover is not None and "cover" in name:
        cmd += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic",
                "-id3v2_version", "3"]  # fmt: skip
    else:
        cmd += ["-map", "0:a"]
    cmd += [*options, "-metadata", "title=ChordChart test", str(target)]
    subprocess.run(cmd, check=True)
    return target


def decode(song: Path, mode: str, decode_only: bool = True, timeout: float = 900) -> dict:
    out_name = f"dec-{mode}-{song.name}.json"
    r.run_as(f"rm -f files/bench/{out_name}", check=False)
    r.adb("shell", "am", "start", "-S", "-W", "-n", f"{r.PACKAGE}/.BenchmarkActivity",
          "--es", "file", f"{r.DEVICE_DIR}/songs/{song.name}", "--es", "out", out_name,
          "--ez", "decode_only", "true" if decode_only else "false", *MODES[mode])  # fmt: skip
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if r.run_as(f"ls files/bench/{out_name}", check=False).endswith(out_name):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"{song.name} {mode}: no result")
    time.sleep(0.3)
    local = OUT / "results" / f"{mode}-{song.name}.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(r.run_as(f"cat files/bench/{out_name}"), encoding="utf-8")
    r.adb("shell", "am", "force-stop", r.PACKAGE)
    return json.loads(local.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--only", nargs="+", help="variant names to run")
    args = parser.parse_args()

    r.wait_for_boot()
    if not args.skip_install:
        r.adb("install", "-r", "-g", str(r.APK))
    r.run_as("mkdir -p files/songs files/bench")
    sources = [
        next(p for p in r.DOWNLOADS.glob(f"youtube-{s}.*") if p.suffix != ".json") for s in SOURCES
    ]
    cover = OUT / "cover.jpg"
    if not cover.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=600x600", "-frames:v", "1", str(cover)], check=True)  # fmt: skip

    files = []
    for source in sources:
        for name, (ext, options) in VARIANTS.items():
            if args.only and name not in args.only:
                continue
            files.append(make(source, name, ext, options, cover))
    existing = set(r.run_as("ls files/songs", check=False).split())
    for f in files:
        if f.name not in existing:
            r.copy_song(f)

    report = {"variants": {}, "full": {}}
    failures = 0
    for f in files:
        results = {mode: decode(f, mode) for mode in MODES}
        hashes = {m: res.get("pcm_sha256") for m, res in results.items()}
        errors = {m: res["error"] for m, res in results.items() if "error" in res}
        same = len(set(hashes.values())) == 1 and not errors
        failures += not same
        used = results["in-process"].get("decoder", {}).get("packets_from", "?")
        line = (f"{f.name:48} {'IDENTICAL' if same else 'DIFFERENT'}  decode: "
                + "  ".join(f"{m} {res.get('decode_s', float('nan')):5.2f} s" for m, res in results.items())
                + f"  [{used}; format changes {results['platform'].get('decoder', {}).get('format_changes', '?')}]")  # fmt: skip
        if errors:
            line += f"  errors: {errors}"
        if not same and not errors:
            line += "  samples: " + ", ".join(
                f"{m} {res.get('samples')}" for m, res in results.items()
            )
        print(line, flush=True)
        report["variants"][f.name] = {"identical": same, "hashes": hashes, "errors": errors,
                                      "decode_s": {m: res.get("decode_s") for m, res in results.items()},
                                      "in_process": used}  # fmt: skip

    if args.full:
        songs = sorted(p for p in r.DOWNLOADS.iterdir() if p.suffix == ".webm")
        for ext, options in (
            ("mp3", VARIANTS["mp3-lame-cbr192-cover"][1]),
            ("m4a", VARIANTS["m4a-aac192"][1]),
        ):
            rows = []
            for song in songs:
                f = make(song, f"full-{ext}", ext, options, None)
                if f.name not in existing:
                    r.copy_song(f)
                platform = decode(f, "platform")
                ours = decode(f, "in-process", decode_only=False)
                same = platform.get("pcm_sha256") == ours.get("pcm_sha256") and "error" not in ours
                failures += not same
                rows.append({"file": f.name, "identical_pcm": same, "platform_decode_s": platform.get("decode_s"),
                             "decode_s": ours.get("decode_s"), "analyze_s": ours.get("analyze_s"),
                             "duration": ours.get("song", {}).get("duration"),
                             "peak_mb": ours.get("memory", {}).get("VmHWM", 0) / 1024})  # fmt: skip
                row = rows[-1]
                print(f"{f.name:40} {'IDENTICAL' if same else 'DIFFERENT'}  platform decode {row['platform_decode_s']:5.1f} s"
                      f"  in-process decode {row['decode_s']:5.2f} s  analysis {row['analyze_s']:5.1f} s"
                      f"  file to chart {row['decode_s'] + row['analyze_s']:5.1f} s", flush=True)  # fmt: skip
            n = len(rows)
            summary = {
                "songs": n,
                "identical_pcm": sum(x["identical_pcm"] for x in rows),
                "platform_decode_mean_s": sum(x["platform_decode_s"] for x in rows) / n,
                "decode_mean_s": sum(x["decode_s"] for x in rows) / n,
                "decode_max_s": max(x["decode_s"] for x in rows),
                "analyze_mean_s": sum(x["analyze_s"] for x in rows) / n,
                "file_to_chart_mean_s": sum(x["decode_s"] + x["analyze_s"] for x in rows) / n,
                "file_to_chart_median_s": sorted(x["decode_s"] + x["analyze_s"] for x in rows)[
                    n // 2
                ],
                "file_to_chart_max_s": max(x["decode_s"] + x["analyze_s"] for x in rows),
                "audio_mean_s": sum(x["duration"] for x in rows) / n,
                "peak_mb_max": max(x["peak_mb"] for x in rows),
            }
            report["full"][ext] = {"summary": summary, "songs": rows}
            print(ext, json.dumps(summary, indent=1), flush=True)

    (OUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
