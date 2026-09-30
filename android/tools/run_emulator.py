# ruff: noqa: E501  (report lines)
"""Run the Android app's analysis on every cached song, on the emulator (or a phone), and
compare it with the Python pipeline.

    uv run python android/tools/run_emulator.py [--modes cpu xnnpack] [--songs ID ...]

For each song: push the original file (webm/opus, m4a, ...), start the debug build's
BenchmarkActivity in a fresh process (so peak memory is per song), wait for its result and
pull it. The phone decodes the file itself (MediaCodec) and analyses it with the C++ core.

Reported per song and in total:
- agreement with Python's Song (android/golden/<song>/stages.json): identical or not,
  chords (share of 0.1 s steps with the same chord), bar lines (within 25 ms), key;
- XNNPACK vs the default CPU provider: beats and bars identical?, speed;
- time (decode, analysis) and peak memory (VmHWM) per song;
- the Beatles evaluation (evaluate/) scored on the phone's results vs Python's.

Results: android/build/emulator/[<out>/]<mode>/<song>.json and summary.json.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANDROID = HERE.parent
ROOT = ANDROID.parent
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT))
from common import DOWNLOADS  # noqa: E402

PACKAGE = "io.github.shanikn.chordchart"
# The app's private storage (debug build: reachable with `run-as`). Files pushed to shared
# storage belong to adb's user, and the app may not read them.
DEVICE_DIR = f"/data/user/0/{PACKAGE}/files"
ADB = Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / "adb.exe"
APK = ANDROID / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
OUT = ANDROID / "build" / "emulator"
GOLDEN = ANDROID / "golden"


def adb(*args: str, check: bool = True, capture: bool = True) -> str:
    result = subprocess.run([str(ADB), *args], capture_output=capture, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip() if capture else ""


def wait_for_boot(timeout: float = 300) -> None:
    adb("wait-for-device")
    deadline = time.monotonic() + timeout
    while adb("shell", "getprop", "sys.boot_completed", check=False) != "1":
        if time.monotonic() > deadline:
            raise RuntimeError("the device didn't finish booting")
        time.sleep(2)


def run_as(command: str, check: bool = True) -> str:
    return adb("shell", f"run-as {PACKAGE} sh -c '{command}'", check=check)


def copy_song(song: Path) -> None:
    """Into the app's private storage: push to /data/local/tmp, then pipe it through
    run-as (which runs as the app, so the app owns the copy)."""
    tmp = f"/data/local/tmp/{song.name}"
    adb("push", str(song), tmp)
    adb("shell", f"cat {tmp} | run-as {PACKAGE} sh -c 'cat > files/songs/{song.name}'")
    adb("shell", "rm", "-f", tmp)


def run_song(song: Path, mode: str, threads: int, timeout: float = 900, attempts: int = 2) -> dict:
    out_name = f"{mode}-{song.stem}.json"
    for attempt in range(1, attempts + 1):
        run_as(f"rm -f files/bench/{out_name}", check=False)
        # -S: stop the app first, so every song starts in a fresh process
        adb("shell", "am", "start", "-S", "-W", "-n", f"{PACKAGE}/.BenchmarkActivity",
            "--es", "file", f"{DEVICE_DIR}/songs/{song.name}", "--es", "out", out_name,
            "--ez", "xnnpack", "true" if mode == "xnnpack" else "false", "--ei", "threads", str(threads))  # fmt: skip
        deadline = time.monotonic() + timeout
        done = False
        started = time.monotonic()
        while time.monotonic() < deadline:
            if run_as(f"ls files/bench/{out_name}", check=False).endswith(out_name):
                done = True
                break
            # the process gone without a result (killed, crashed, or never started): retry
            if time.monotonic() - started > 10 and not adb("shell", "pidof", PACKAGE, check=False):
                break
            time.sleep(1)
        if done:
            break
        print(f"   {song.name} {mode}: no result (attempt {attempt}); retrying", flush=True)
    else:
        raise RuntimeError(f"{song.name}: no result after {attempts} attempts")
    time.sleep(0.5)  # the file is written in one go; give the writer a moment to close it
    local = OUT / mode / f"{song.stem}.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(run_as(f"cat files/bench/{out_name}"), encoding="utf-8")
    adb("shell", "am", "force-stop", PACKAGE)
    return json.loads(local.read_text(encoding="utf-8"))


def chord_at(bars: list, t: float) -> str:
    for bar in bars:
        if bar["start"] <= t < bar["end"]:
            current = ""
            for chord in bar["chords"]:
                if chord["time"] <= t + 1e-9:
                    current = chord["harte"]
            return current or (bar["chords"][0]["harte"] if bar["chords"] else "")
    return ""


def agreement(ours: dict, theirs: dict) -> dict:
    duration = theirs["duration"]
    steps = [i * 0.1 for i in range(int(duration / 0.1) + 1) if i * 0.1 < duration]
    same = sum(chord_at(ours["bars"], t) == chord_at(theirs["bars"], t) for t in steps)
    matched = sum(
        any(abs(o["start"] - b["start"]) <= 0.025 for o in ours["bars"]) for b in theirs["bars"]
    )
    fields = ("bars", "bpm", "meter")
    return {
        # compared as values: the core writes 2.0 as 2, Python as 2.0
        "identical": all(ours[f] == theirs[f] for f in fields)
        and ours["key"]["tonic"] == theirs["key"]["tonic"]
        and ours["key"]["mode"] == theirs["key"]["mode"],
        "chords": same / max(1, len(steps)),
        "bar_lines": matched / max(1, len(theirs["bars"])),
        "key_same": (ours["key"]["tonic"], ours["key"]["mode"])
        == (theirs["key"]["tonic"], theirs["key"]["mode"]),
    }


def beatles_scores(results: dict[str, dict]) -> dict:
    """The Beatles evaluation's chart and beat scores, on the phone's results (same code
    and alignment as evaluate/run_eval.py)."""
    import tempfile

    import numpy as np

    from chordchart.beats import Beats
    from chordchart.fetch import decode_section, read_wav
    from chordchart.model import Bar, ChordEvent, Key, Song
    from evaluate import align, data, score

    data.ensure_annotations()
    rows = {}
    for song in data.load_songs():
        stem = "youtube-" + song.link.split("v=")[1]
        if stem not in results or "song" not in results[stem]:
            continue
        s = results[stem]["song"]
        ref_intervals, ref_labels = data.read_chords(song.chord_file)
        ref_beats, ref_positions = data.read_beats(song.beat_file)
        path = next(p for p in DOWNLOADS.glob(stem + ".*") if p.suffix != ".json")
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            decoded = decode_section(path, wav)
            samples = read_wav(wav)
        alignment = align.estimate_offset(samples, ref_beats, ref_intervals, ref_labels)
        ref_intervals, ref_labels, ref_beats, ref_positions = score.shift_reference(
            ref_intervals, ref_labels, ref_beats, ref_positions, alignment.offset, decoded.duration
        )
        bars = [
            Bar(
                b["index"],
                b["start"],
                b["end"],
                [ChordEvent(c["beat"], c["time"], c["symbol"], c["harte"]) for c in b["chords"]],
            )
            for b in s["bars"]
        ]
        song_obj = Song(title=song.title, source=song.link, duration=s["duration"],
                        key=Key(s["key"]["tonic"], s["key"]["mode"], s["key"]["confidence"]),
                        bpm=s["bpm"], meter=s["meter"], bars=bars)  # fmt: skip
        chart = score.chord_scores(ref_intervals, ref_labels, score.chart_segments(song_obj))
        beats = score.beat_scores(
            ref_beats,
            ref_positions,
            Beats(s["beats"]["times"], s["beats"]["positions"], s["bpm"], s["meter"]),
        )
        rows[song.slug] = {
            "weight": float(np.asarray(ref_intervals)[-1, 1]),
            "chart": chart,
            "beats": beats,
        }
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", nargs="+", default=["cpu", "xnnpack"])
    parser.add_argument("--songs", nargs="+", help="song ids (default: every cached song)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument(
        "--out", help="results under build/emulator/<out>/ (default: build/emulator/)"
    )
    args = parser.parse_args()
    global OUT
    if args.out:
        OUT = OUT / args.out

    songs = sorted(p for p in DOWNLOADS.iterdir() if p.suffix in {".webm", ".m4a", ".mp3", ".opus"})
    if args.songs:
        songs = [p for p in songs if any(s in p.stem for s in args.songs)]
    wait_for_boot()
    device = (
        adb("shell", "getprop", "ro.product.model")
        + ", Android "
        + adb("shell", "getprop", "ro.build.version.release")
    )
    print(f"device: {device}; {len(songs)} songs; modes {args.modes}", flush=True)
    if not args.skip_install:
        adb("install", "-r", "-g", str(APK))
    run_as("mkdir -p files/songs files/bench")
    existing = set(run_as("ls files/songs", check=False).split())
    for song in songs:
        if song.name not in existing:
            copy_song(song)

    results: dict[str, dict[str, dict]] = {m: {} for m in args.modes}
    for song in songs:
        for mode in args.modes:
            r = run_song(song, mode, args.threads)
            results[mode][song.stem] = r
            golden = GOLDEN / song.stem / "stages.json"
            line = f"{song.stem:22} {mode:8}"
            if "error" in r:
                line += f" ERROR {r['error']}"
            else:
                if golden.exists():
                    a = agreement(r["song"], json.loads(golden.read_text(encoding="utf-8"))["song"])
                    r["agreement"] = a
                    line += (f" {'IDENTICAL' if a['identical'] else 'differs  '} chords {100 * a['chords']:5.1f}%"
                             f" bars {100 * a['bar_lines']:5.1f}% key {'same' if a['key_same'] else 'DIFF'}")  # fmt: skip
                line += (f" | decode {r['decode_s']:5.1f} s analyse {r['analyze_s']:5.1f} s"
                         f" peak {r['memory']['VmHWM'] / 1024:5.0f} MB")  # fmt: skip
            print(line, flush=True)

    summary: dict = {"device": device, "modes": {}}
    for mode, rows in results.items():
        ok = [r for r in rows.values() if "song" in r]
        with_golden = [r for r in ok if "agreement" in r]
        summary["modes"][mode] = {
            "songs": len(rows),
            "errors": len(rows) - len(ok),
            "identical": sum(r["agreement"]["identical"] for r in with_golden),
            "chords": sum(r["agreement"]["chords"] for r in with_golden) / max(1, len(with_golden)),
            "bar_lines": sum(r["agreement"]["bar_lines"] for r in with_golden)
            / max(1, len(with_golden)),
            "key_same": sum(r["agreement"]["key_same"] for r in with_golden),
            "decode_s": sum(r["decode_s"] for r in ok),
            "analyze_s": sum(r["analyze_s"] for r in ok),
            "audio_s": sum(r["song"]["duration"] for r in ok),
            "peak_mb_max": max((r["memory"]["VmHWM"] for r in ok), default=0) / 1024,
            "beats_s": sum(r["song"]["timings"].get("beats", 0) for r in ok),
        }
    if {"cpu", "xnnpack"} <= set(results):
        both = [
            s
            for s in results["cpu"]
            if "song" in results["cpu"][s] and "song" in results["xnnpack"].get(s, {})
        ]
        same = sum(
            json.dumps(results["cpu"][s]["song"]["bars"]) == json.dumps(results["xnnpack"][s]["song"]["bars"])
            and results["cpu"][s]["song"]["beats"] == results["xnnpack"][s]["song"]["beats"]
            for s in both
        )  # fmt: skip
        summary["xnnpack_vs_cpu"] = {"songs": len(both), "beats_and_bars_identical": same}
    for mode, rows in results.items():
        try:
            summary["modes"][mode]["beatles"] = beatles_scores(rows)
        except Exception as exc:  # the evaluation needs its annotations download
            summary["modes"][mode]["beatles_error"] = str(exc)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "modes"}, indent=1))
    for mode, m in summary["modes"].items():
        print(f"{mode}: identical {m['identical']}/{m['songs']}, chords {100 * m['chords']:.2f}%, bar lines "
              f"{100 * m['bar_lines']:.2f}%, key {m['key_same']}/{m['songs']}, errors {m['errors']}; "
              f"{m['audio_s'] / 60:.0f} min of audio: decode {m['decode_s']:.0f} s, analysis {m['analyze_s']:.0f} s "
              f"(beat model {m['beats_s']:.0f} s), peak {m['peak_mb_max']:.0f} MB")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
