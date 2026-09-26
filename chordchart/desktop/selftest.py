"""`ChordChart.exe --self-test report.json`: prove a packaged app works, headless.

Checks what packaging can break: the bundled ffmpeg and JavaScript runtime run,
madmom's models load with OpenCV's fast path, yt-dlp imports, a synthesized song is
analysed correctly with the beat-tracking worker processes (which re-run the frozen
exe), and those workers are gone afterwards. Writes a JSON report; exit code 0 = pass.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import traceback
import wave
from pathlib import Path

import numpy as np

# | C | G | Am | F  C | at 100 BPM, twice: the same progression the test suite uses.
_TRIADS = {
    "C": (130.8, 164.8, 196.0),
    "G": (98.0, 123.5, 146.8),
    "Am": (110.0, 130.8, 164.8),
    "F": (87.3, 110.0, 130.8),
}
_PROGRESSION = [("C", 0, 4), ("G", 4, 4), ("Am", 8, 4), ("F", 12, 2), ("C", 14, 2)]
_PROGRESSION += [(c, s + 16, d) for c, s, d in _PROGRESSION]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(report_path: str) -> int:
    checks: dict[str, dict] = {}

    def check(name, fn):
        began = time.perf_counter()
        try:
            checks[name] = {"ok": True, "detail": fn()}
        except Exception as exc:
            checks[name] = {
                "ok": False,
                "detail": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        checks[name]["seconds"] = round(time.perf_counter() - began, 2)

    check("ffmpeg", _ffmpeg)
    check("deno", _deno)
    check("yt-dlp", _ytdlp)
    check("opencv fast path", _opencv)
    check("analysis with worker processes", _analysis)

    report = {"ok": all(c["ok"] for c in checks.values()), "checks": checks}
    Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def _ffmpeg():
    from chordchart import bundled

    path = bundled.ffmpeg_path()
    out = subprocess.run(
        [path, "-version"], capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=30
    ).stdout
    return {"path": path, "version": out.splitlines()[0]}


def _deno():
    from chordchart import bundled

    path = bundled.deno_path()
    out = subprocess.run(
        [path, "--version"], capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=30
    ).stdout
    return {"path": path, "version": out.splitlines()[0]}


def _ytdlp():
    import yt_dlp
    import yt_dlp.version
    import yt_dlp_ejs  # noqa: F401

    yt_dlp.YoutubeDL({"quiet": True})
    return {"version": yt_dlp.version.__version__, "from": str(Path(yt_dlp.__file__).parent)}


def _opencv():
    import cv2
    import madmom.ml.nn.layers as layers

    if layers._convolve_opencv is None:
        raise RuntimeError("madmom is not using OpenCV (slow path)")
    return {"cv2": cv2.__version__}


def _analysis():
    from chordchart.pipeline import DEFAULT_THREADS, analyze
    from chordchart.processors import Processors

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "selftest.wav"
        _write_progression(wav)
        procs = Processors(num_threads=DEFAULT_THREADS)
        workers = [w for pool in procs.pools() for w in pool._pool]
        try:
            song = analyze(wav, processors=procs, cache=False)
        finally:
            procs.close()
        still_running = sum(w.is_alive() for w in workers)
    first = [bar.chords[0].symbol for bar in song.bars[:3]]
    problems = []
    if abs(song.bpm - 100) > 3:
        problems.append(f"tempo {song.bpm:.1f}, expected 100")
    if first != ["C", "G", "Am"]:
        problems.append(f"first chords {first}, expected ['C', 'G', 'Am']")
    if still_running:
        problems.append(f"{still_running} worker process(es) still running after close")
    if problems:
        raise AssertionError("; ".join(problems))
    return {
        "bpm": round(song.bpm, 1),
        "first_chords": first,
        "bars": len(song.bars),
        "worker_processes": len(workers),
        "elapsed": song.elapsed,
    }


def _write_progression(path: Path, bpm: float = 100.0, sr: int = 44_100) -> None:
    beat = 60.0 / bpm
    total_beats = max(s + d for _, s, d in _PROGRESSION)
    n = int((total_beats * beat + 1.0) * sr)
    t = np.arange(n) / sr
    x = np.zeros(n)
    for chord, start, length in _PROGRESSION:
        a, b = max(0.0, (start - 0.5) * beat), (start + length - 0.5) * beat
        i, j = int(a * sr), int(b * sr)
        tt = t[i:j]
        voice = sum(np.sin(2 * np.pi * f * k * tt) / k for f in _TRIADS[chord] for k in range(1, 6))
        x[i:j] += voice * np.exp(-(tt - a) * 0.8)
    click = np.hanning(600)
    for k in range(total_beats):
        i = int(k * beat * sr)
        x[i : i + len(click)] += (6.0 if k % 4 == 0 else 3.0) * click
    samples = (0.6 * x / np.max(np.abs(x)) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(samples.tobytes())
