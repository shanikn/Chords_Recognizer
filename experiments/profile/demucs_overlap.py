"""Demucs overlap 0.25 (the app's setting) vs 0.1: separation time, and how much the
transcribed notes change.

shifts=1 (Demucs's default, which the app uses) shifts the input by a random 0-0.5 s;
the app now fixes that with a seed (stems.SEED). Per song this separates three times,
transcribes all four stems of each, and compares notes with mir_eval (onset within
50 ms and same pitch; offsets ignored):

- 0.25a (seed 0) vs 0.25b (seed 1): what a different random shift alone changes, i.e.
  how much the notes varied between runs before the seed was fixed;
- 0.25a (seed 0) vs 0.1 (seed 0):   the change from the lower overlap, same shift.

It also re-runs 0.25 with seed 0 on the first song to confirm the stems are identical.

    uv run python experiments/profile/demucs_overlap.py [song ...]
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

SONGS = {
    "yesterday": "youtube-NrgmdOz227I.webm",
    "adele": "youtube-hLQl3WQQoQ0.webm",
    "cheek": "youtube-20iOlPwz0J0.webm",
}
RUNS = (("0.25a", 0.25, 0), ("0.25b", 0.25, 1), ("0.1", 0.1, 0))


def separate(model, mix, overlap, seed):
    import demucs.apply

    from chordchart.notes import stems

    original = demucs.apply.apply_model

    def with_overlap(*args, **kwargs):
        kwargs["overlap"] = overlap
        return original(*args, **kwargs)

    stems_module_apply = demucs.apply.apply_model
    demucs.apply.apply_model = with_overlap
    try:
        began = time.perf_counter()
        out = stems.run_demucs(model, mix, seed=seed)
        return out, time.perf_counter() - began
    finally:
        demucs.apply.apply_model = stems_module_apply


def f1(reference, estimate):
    from mir_eval.transcription import precision_recall_f1_overlap

    def arrays(notes):
        if not notes:
            return np.zeros((0, 2)), np.zeros(0)
        intervals = np.array([[n.start, max(n.end, n.start + 1e-3)] for n in notes])
        hz = 440.0 * 2 ** ((np.array([n.pitch for n in notes]) - 69) / 12)
        return intervals, hz

    if not reference and not estimate:
        return 1.0
    if not reference or not estimate:
        return 0.0
    (ri, rp), (ei, ep) = arrays(reference), arrays(estimate)
    return precision_recall_f1_overlap(ri, rp, ei, ep, onset_tolerance=0.05, offset_ratio=None)[2]


def main():
    from chordchart.notes import stems, transcribe
    from chordchart.notes.model import INSTRUMENTS
    from chordchart.notes.select import choose, stem_levels

    names = sys.argv[1:] or list(SONGS)
    model = stems.load_model()
    report = {}
    for name in names:
        mix = stems.decode_stereo(DOWNLOADS / SONGS[name], 0.0, 10_000.0)
        notes, times, chosen = {}, {}, {}
        for label, overlap, seed in RUNS:
            separated, seconds = separate(model, mix, overlap, seed)
            if label == "0.25a" and name == names[0]:
                again, _ = separate(model, mix, overlap, seed)
                same = all(np.array_equal(separated[k], again[k]) for k in separated)
                report["deterministic"] = same
                print(f"same seed twice -> identical stems: {same}", flush=True)
            times[label] = round(seconds, 1)
            kept = {k: separated[k] for k in INSTRUMENTS}
            chosen[label] = choose(stem_levels(kept))[0]
            with tempfile.TemporaryDirectory() as tmp:
                folder = stems._store(Path(tmp), kept)
                notes[label] = {k: transcribe.transcribe(folder.path(k), k) for k in INSTRUMENTS}
            print(f"{name} overlap {label}: {seconds:.1f} s, auto stem {chosen[label]}", flush=True)
        song = {
            "seconds": len(mix[0]) / 44100,
            "separation_s": times,
            "auto_stem": chosen,
            "f1": {},
        }
        for k in INSTRUMENTS:
            a, b, low = notes["0.25a"][k], notes["0.25b"][k], notes["0.1"][k]
            song["f1"][k] = {
                "notes_0.25a": len(a),
                "notes_0.1": len(low),
                "noise_0.25a_vs_0.25b": round(f1(a, b), 3),
                "change_0.25a_vs_0.1": round(f1(a, low), 3),
            }
        report[name] = song
        print(json.dumps({name: song}, indent=1), flush=True)
    Path(__file__).with_name("demucs_overlap_result.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
