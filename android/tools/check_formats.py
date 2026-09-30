# ruff: noqa: E501  (report lines)
"""Does the phone decode every common format like the desktop does?

    uv run python android/tools/check_formats.py [SONG_ID]

Converts one cached song (default: Yesterday) with ffmpeg into MP3, AAC (m4a), WAV,
Ogg Vorbis, FLAC and Ogg Opus, at 44.1 kHz and, for FLAC and Opus, 48 kHz (so the phone
also resamples). Each file is analysed by the Python pipeline and by the app on the
emulator (MediaCodec decodes it there), and the two charts are compared.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_emulator as r  # noqa: E402

FORMATS = {  # name: (extension, ffmpeg options)
    "mp3-44k": ("mp3", ["-ar", "44100", "-c:a", "libmp3lame", "-b:a", "192k"]),
    "aac-44k": ("m4a", ["-ar", "44100", "-c:a", "aac", "-b:a", "192k"]),
    "wav-44k": ("wav", ["-ar", "44100", "-c:a", "pcm_s16le"]),
    "vorbis-44k": ("ogg", ["-ar", "44100", "-c:a", "libvorbis", "-q:a", "6"]),
    "flac-48k": ("flac", ["-ar", "48000", "-c:a", "flac"]),
    "opus-48k": ("opus", ["-ar", "48000", "-c:a", "libopus", "-b:a", "160k"]),
}


def main() -> int:
    from chordchart.fetch import find_ffmpeg
    from chordchart.pipeline import analyze
    from chordchart.processors import Processors

    song_id = sys.argv[1] if len(sys.argv) > 1 else "NrgmdOz227I"
    source = next(p for p in r.DOWNLOADS.glob(f"youtube-{song_id}.*") if p.suffix != ".json")
    folder = r.ANDROID / "build" / "formats"
    folder.mkdir(parents=True, exist_ok=True)
    r.wait_for_boot()
    r.run_as("mkdir -p files/songs files/bench")
    rows = []
    with Processors(num_threads=4) as procs:
        for name, (ext, options) in FORMATS.items():
            path = folder / f"{name}.{ext}"
            if not path.exists():
                subprocess.run(
                    [
                        find_ffmpeg(),
                        "-v",
                        "error",
                        "-y",
                        "-i",
                        str(source),
                        "-vn",
                        "-ac",
                        "2",
                        *options,
                        str(path),
                    ],
                    check=True,
                )
            python_song = json.loads(analyze(path, processors=procs, cache=False).to_json())
            r.copy_song(path)
            result = r.run_song(path, "cpu", 4)
            if "error" in result:
                rows.append((name, None, result["error"]))
                print(f"{name:11} ERROR {result['error']}", flush=True)
                continue
            a = r.agreement(result["song"], python_song)
            codec = result.get("decoder", {}).get("codec", "?")
            rows.append((name, a, codec))
            print(f"{name:11} {codec:34} {'IDENTICAL' if a['identical'] else 'differs  '} chords {100 * a['chords']:5.1f}% "
                  f"bars {100 * a['bar_lines']:5.1f}% key {'same' if a['key_same'] else 'DIFF'} | decode {result['decode_s']:.1f} s", flush=True)  # fmt: skip
    ok = all(a is not None for _, a, _ in rows)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
