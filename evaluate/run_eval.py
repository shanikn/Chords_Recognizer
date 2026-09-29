"""Evaluate chordchart on annotated songs (evaluate/songs.toml), per beat tracker.

    uv run python -m evaluate.run_eval                       # madmom and Beat This! + DBN
    uv run python -m evaluate.run_eval --trackers madmom     # just one
    uv run python -m evaluate.run_eval --songs yesterday michelle --no-save

For every song: download (via chordchart's cache) the audio and (once) the annotations,
find the time offset between them (align.py), run the full pipeline with each beat
tracker (pipeline.analyze(beats_fn=...), no analysis cache so all models run), and score
- chords (mir_eval.chord) at each stage: raw (the recognizer), beat_sync, chart;
- beats (mir_eval.beat F-measure, CMLt, AMLt), downbeats (F-measure) and meter.
Means are weighted by song duration. Results go to evaluate/results/<date>-<sha>.json.
Beat This! + DBN needs its model first: see evaluate/trackers.py (DOWNLOAD_STEP).
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from chordchart.errors import ChordChartError
from chordchart.fetch import decode_section, read_wav
from chordchart.pipeline import DEFAULT_THREADS, analyze
from chordchart.processors import Processors
from chordchart.sources import resolve_source
from evaluate import align, data, score
from evaluate.trackers import TRACKERS

RESULTS = Path(__file__).resolve().parent / "results"
STAGES = ("raw", "beat_sync", "chart")
MUSIC_CUT = 1.0  # seconds: audio ending this much before the annotated music: skip
MAX_EXTRA = 10.0  # seconds of audio after the music: more means another version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evaluate.run_eval", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--trackers", nargs="+", choices=list(TRACKERS), default=list(TRACKERS))
    parser.add_argument("--songs", nargs="+", metavar="SLUG", help="only these songs")
    parser.add_argument("--no-save", action="store_true", help="don't write evaluate/results/")
    parser.add_argument(
        "--align-only", action="store_true", help="only find the time offsets (no models)"
    )
    args = parser.parse_args(argv)

    data.ensure_annotations()
    songs = [s for s in data.load_songs() if not args.songs or s.slug in args.songs]
    results: dict = {}
    with Processors(num_threads=DEFAULT_THREADS) as processors:
        trackers = {name: TRACKERS[name](processors) for name in args.trackers}
        for song in songs:
            print(f"\n== {song.title}", flush=True)
            try:
                results[song.slug] = evaluate_song(song, trackers, processors, args.align_only)
            except ChordChartError as exc:  # e.g. a failed download: skip, keep going
                print(f"   SKIPPED: {exc}")
                results[song.slug] = {"title": song.title, "skipped": str(exc)}
    if args.align_only:
        return 0

    summary = summarize(results, args.trackers)
    print_report(results, summary, args.trackers)
    if not args.no_save:
        path = save(results, summary, args.trackers)
        print(f"\nsaved {path}")
    return 0


def evaluate_song(song: data.EvalSong, trackers: dict, processors, align_only=False) -> dict:
    ref_intervals, ref_labels = data.read_chords(song.chord_file)
    ref_beats, ref_positions = data.read_beats(song.beat_file)

    resolved = resolve_source(song.link)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        decoded = decode_section(resolved.path, wav)
        samples = read_wav(wav)
    alignment = align.estimate_offset(samples, ref_beats, ref_intervals, ref_labels)
    annotated = float(ref_intervals[-1, 1])
    # Annotations end with the CD's silence; compare the audio with the end of the music.
    music_end = max(
        float(e) for (_, e), lab in zip(ref_intervals, ref_labels, strict=True) if lab != "N"
    )
    gap = decoded.duration - (music_end + alignment.offset)
    print(f"   offset {alignment.offset:+.2f} s (chords say {alignment.coarse:+.2f}, "
          f"strength {alignment.strength:.1f}); audio {decoded.duration:.1f} s vs annotation "
          f"music end {music_end:.1f} s + offset (gap {gap:+.1f} s)")  # fmt: skip
    print(f"   {'':14} {HEADER}")
    entry = {
        "title": song.title, "link": song.link, "duration": annotated,
        "offset": alignment.offset, "offset_coarse": alignment.coarse,
        "alignment_strength": alignment.strength, "duration_gap": gap, "trackers": {},
    }  # fmt: skip
    if align_only:
        return entry
    if not -MUSIC_CUT <= gap <= MAX_EXTRA:
        print(f"   SKIPPED: the audio doesn't match the annotation's length ({gap:+.1f} s)")
        entry["skipped"] = f"duration gap {gap:+.1f} s"
        return entry

    ref_intervals, ref_labels, ref_beats, ref_positions = score.shift_reference(
        ref_intervals, ref_labels, ref_beats, ref_positions, alignment.offset, decoded.duration
    )
    for name, beats_fn in trackers.items():
        recorded = []

        def recording(audio, beats_fn=beats_fn, recorded=recorded):
            recorded.append(beats_fn(audio))
            return recorded[-1]

        began = time.perf_counter()
        result = analyze(song.link, processors=processors, beats_fn=recording)
        seconds = time.perf_counter() - began
        stages = {
            "raw": result.debug["raw"],
            "beat_sync": result.debug["beat_sync"],
            "chart": score.chart_segments(result),
        }
        chords = {
            stage: score.chord_scores(ref_intervals, ref_labels, segs)
            for stage, segs in stages.items()
        }
        beats = score.beat_scores(ref_beats, ref_positions, recorded[0])
        entry["trackers"][name] = {
            "chords": chords, "beats": beats, "beat_seconds": result.timings.get("beats"),
            "total_seconds": seconds, "bpm": result.bpm,
        }  # fmt: skip
        meter = f"{beats['meter_est']}/{beats['meter_ref']}"
        print(
            f"   {name:14} {_cells(chords, beats)} {meter:>6} {result.timings.get('beats', 0):8.1f}"
        )
    return entry


def summarize(results: dict, tracker_names: list[str]) -> dict:
    scored = [r for r in results.values() if "skipped" not in r]
    weights = [r["duration"] for r in scored]
    summary = {}
    for name in tracker_names:
        runs = [r["trackers"][name] for r in scored]
        chords = {
            stage: {
                m: score.weighted_mean([run["chords"][stage][m] for run in runs], weights)
                for m in score.CHORD_METRICS
            }
            for stage in STAGES
        }
        beats = {
            m: score.weighted_mean([run["beats"][m] for run in runs], weights)
            for m in ("beat_f", "cmlt", "amlt", "downbeat_f")
        }
        beats["meter_correct"] = sum(
            run["beats"]["meter_est"] == run["beats"]["meter_ref"] for run in runs
        )
        beats["octave_songs"] = [
            slug
            for slug, r in results.items()
            if "skipped" not in r and r["trackers"][name]["beats"]["octave"]
        ]
        summary[name] = {
            "chords": chords, "beats": beats, "songs": len(runs),
            "beat_seconds": sum(run["beat_seconds"] or 0 for run in runs),
        }  # fmt: skip
    return summary


def print_report(results: dict, summary: dict, tracker_names: list[str]) -> None:
    print("\n" + "=" * 100)
    print("duration-weighted means over", next(iter(summary.values()))["songs"], "songs")
    print(f"{'tracker':14} {HEADER}")
    for name in tracker_names:
        s = summary[name]
        meter = f"{s['beats']['meter_correct']}/{s['songs']}"
        print(f"{name:14} {_cells(s['chords'], s['beats'])} {meter:>6} {s['beat_seconds']:8.0f}")
    for name in tracker_names:
        octave = summary[name]["beats"]["octave_songs"]
        if octave:
            level = "right beats at another metrical level (AMLt >= 0.8, CMLt < 0.5)"
            print(f"{name}: {level}: {', '.join(octave)}")
    skipped = [f"{slug} ({r['skipped']})" for slug, r in results.items() if "skipped" in r]
    if skipped:
        print("skipped:", ", ".join(skipped))


# Table columns: (title, width); the song lines and the summary share them.
COLUMNS = [
    ("majmin raw", 10), ("beat_sync", 10), ("chart", 8), ("root chart", 11),
    ("beat F", 7), ("CMLt", 6), ("AMLt", 6), ("down F", 7),
]  # fmt: skip
HEADER = " ".join(f"{t:>{w}}" for t, w in COLUMNS) + f" {'meter':>6} {'beats s':>8}"


def _cells(chords: dict, beats: dict) -> str:
    values = [
        chords["raw"]["majmin"], chords["beat_sync"]["majmin"], chords["chart"]["majmin"],
        chords["chart"]["root"], beats["beat_f"], beats["cmlt"], beats["amlt"], beats["downbeat_f"],
    ]  # fmt: skip
    return " ".join(f"{v:{w}.3f}" for v, (_, w) in zip(values, COLUMNS, strict=True))


def save(results: dict, summary: dict, tracker_names: list[str]) -> Path:
    RESULTS.mkdir(exist_ok=True)
    sha = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "chordchart"], capture_output=True, text=True
        ).stdout.strip()
    )
    stamp = datetime.date.today().isoformat()
    path = RESULTS / f"{stamp}-{sha}{'-dirty' if dirty else ''}-{'+'.join(tracker_names)}.json"
    payload = {
        "date": stamp,
        "git": sha,
        "dirty": dirty,
        "trackers": tracker_names,
        "summary": summary,
        "songs": results,
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


if __name__ == "__main__":
    sys.exit(main())
