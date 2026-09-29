"""Profile the whole app: startup, chord analysis per stage, repeated analysis, notes
(Demucs, basic-pitch, quantize, MIDI, MusicXML), what a chords-only run imports, and peak
memory per phase (whole process tree, sampled every 20 ms).

    uv run --with psutil python experiments/profile/profile_pipeline.py [--songs a b] [--notes SONG]
        [--madmom-conv]      madmom's own convolution (before chordchart.fastconv)
        [--dbn-threads N]    Beat This!'s DBN: 1 = in-process, 2 = worker pool

The work runs in a child process ("worker"); the parent samples its memory and tags it
with the phase the worker announces. Songs are cached downloads (paths, so --refresh-like
runs re-run the models without re-downloading).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
from common import DOWNLOADS  # noqa: E402

SONGS = {
    "yesterday": "youtube-NrgmdOz227I.webm",  # 2:06
    "adele": "youtube-hLQl3WQQoQ0.webm",  # 4:45
    "cheek": "youtube-20iOlPwz0J0.webm",  # 5:52
}
NOTES_SONG = "yesterday"
HEAVY = ("torch", "demucs", "basic_pitch", "librosa", "onnxruntime", "sklearn", "numba")


def emit(kind: str, **data) -> None:
    print(json.dumps({"kind": kind, **data}), flush=True)


def worker(song_keys: list[str], notes_key: str | None, madmom_conv: bool = False, dbn_threads=None) -> None:
    if dbn_threads is not None:
        import chordchart.beat_this

        make_dbn = chordchart.beat_this.make_dbn
        chordchart.beat_this.make_dbn = lambda beats_per_bar, threads=1: make_dbn(beats_per_bar, dbn_threads)
    if madmom_conv:
        import chordchart.fastconv

        chordchart.fastconv.accelerated = lambda processor: processor
    began = time.perf_counter()
    from functools import partial

    from chordchart.pipeline import DEFAULT_THREADS, analyze
    from chordchart.processors import Processors

    emit("time", what="import pipeline", seconds=time.perf_counter() - began)

    emit("phase", name="load chord models")
    t = time.perf_counter()
    procs = Processors(num_threads=DEFAULT_THREADS)
    emit("time", what="load chord+beat models (Processors)", seconds=time.perf_counter() - t)

    for key in song_keys:
        path = DOWNLOADS / SONGS[key]
        emit("phase", name=f"analyze {key} (models run)")
        t = time.perf_counter()
        song = analyze(path, processors=procs, refresh=True)  # models run; result cached
        emit("analysis", song=key, run="first", duration=song.duration, elapsed=song.elapsed,
             wall=time.perf_counter() - t, timings=song.timings)  # fmt: skip
        emit("phase", name=f"analyze {key} again (cache)")
        t = time.perf_counter()
        again = analyze(path, processors=procs)
        emit("analysis", song=key, run="repeat", duration=again.duration, elapsed=again.elapsed,
             wall=time.perf_counter() - t, timings=again.timings)  # fmt: skip
    emit("imports", after="chord analyses", loaded=[m for m in HEAVY if m in sys.modules])

    if notes_key:
        from chordchart.notes import stems, transcribe
        from chordchart.notes.midi import to_midi
        from chordchart.notes.musicxml import to_musicxml
        from chordchart.notes.pipeline import transcribe_notes

        path = DOWNLOADS / SONGS[notes_key]
        emit("phase", name="load Demucs")
        t = time.perf_counter()
        stems.load_model()
        emit("time", what="load Demucs model", seconds=time.perf_counter() - t)
        emit("phase", name="load basic-pitch")
        t = time.perf_counter()
        transcribe._load_model()
        emit("time", what="load basic-pitch model", seconds=time.perf_counter() - t)
        analyze_fn = partial(analyze, processors=procs)
        emit("phase", name=f"notes {notes_key} (Demucs runs)")
        t = time.perf_counter()
        result = transcribe_notes(path, refresh=True, analyze_fn=analyze_fn)
        emit("notes", song=notes_key, run="first", wall=time.perf_counter() - t,
             timings=result.timings, notes=len(result.notes))  # fmt: skip
        emit("phase", name=f"notes {notes_key} again (stems cached)")
        t = time.perf_counter()
        result = transcribe_notes(path, analyze_fn=analyze_fn)
        emit("notes", song=notes_key, run="repeat", wall=time.perf_counter() - t,
             timings=result.timings, notes=len(result.notes))  # fmt: skip
        for what, fn in (("MIDI", to_midi), ("MusicXML", to_musicxml)):
            t = time.perf_counter()
            data = fn(result)
            emit("time", what=f"write {what} ({len(data) // 1024} KB)", seconds=time.perf_counter() - t)
    procs.close()
    emit("done")


def tree_rss(ps) -> int:
    import psutil

    try:
        total = ps.memory_info().rss
        for child in ps.children(recursive=True):
            try:
                total += child.memory_info().rss
            except psutil.Error:
                pass
        return total
    except psutil.Error:
        return 0


def main() -> None:
    import psutil

    args = sys.argv[1:]
    songs = list(SONGS)
    notes = NOTES_SONG
    if "--songs" in args:
        i = args.index("--songs")
        songs = []
        for a in args[i + 1 :]:
            if a.startswith("--"):
                break
            songs.append(a)
    if "--notes" in args:
        i = args.index("--notes")
        notes = None if args[i + 1] == "none" else args[i + 1]
    cmd = [sys.executable, str(HERE), "--worker", json.dumps(songs), json.dumps(notes)]
    if "--madmom-conv" in args:
        cmd.append("--madmom-conv")
    if "--dbn-threads" in args:
        cmd += ["--dbn-threads", args[args.index("--dbn-threads") + 1]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    ps = psutil.Process(proc.pid)
    phase = ["start"]
    peaks: dict[str, int] = {}
    events = []

    def read() -> None:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            event = json.loads(line)
            events.append(event)
            if event["kind"] == "phase":
                phase[0] = event["name"]
            print(line, flush=True)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    while proc.poll() is None:
        peaks[phase[0]] = max(peaks.get(phase[0], 0), tree_rss(ps))
        time.sleep(0.02)
    reader.join(timeout=5)
    print(json.dumps({"kind": "memory", "peak_mb_by_phase": {k: round(v / 2**20) for k, v in peaks.items()}}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        dbn = int(sys.argv[sys.argv.index("--dbn-threads") + 1]) if "--dbn-threads" in sys.argv else None
        worker(json.loads(sys.argv[2]), json.loads(sys.argv[3]), "--madmom-conv" in sys.argv, dbn)
    else:
        main()
