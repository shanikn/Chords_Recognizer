"""Beat This! + DBN: does a stiffer tempo (higher transition_lambda) stop the mid-song
switches between metrical levels, and what does it cost on the annotated songs?

For the cached songs flagged by check_songs.py (bars of uneven length) it reports the
bar-length spread; for the evaluation songs, mir_eval beat F, CMLt and downbeat F
against the Isophonics annotations (offsets from the latest evaluate/results file).
The model runs once per song; only the DBN changes.

    uv run python experiments/beat_this/dbn_lambda.py
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))
from common import DOWNLOADS  # noqa: E402

from chordchart import beat_this  # noqa: E402
from chordchart.beats import _beats  # noqa: E402
from chordchart.fetch import decode_section, read_wav  # noqa: E402
from chordchart.sources import resolve_source  # noqa: E402
from evaluate import data, score  # noqa: E402

LAMBDAS = (100, 300, 1000, 3000)
UNEVEN = {  # flagged by check_songs.py on 2026-09-28
    "Apple Juice": "youtube-67vzn-F4-zU",
    "Fur Elise": "youtube-q9bU12gXUyM",
    "I Knew You Were Trouble": "youtube-TqAollrUJdA",
    "Maneater": "youtube-PLolag3YSYU",
    "non-Latin title (ju01gPz-8HI)": "youtube-ju01gPz-8HI",
}


def dbn(lam):
    from madmom.features.downbeats import DBNDownBeatTrackingProcessor

    return DBNDownBeatTrackingProcessor(
        beats_per_bar=[3, 4], min_bpm=55.0, max_bpm=215.0, fps=beat_this.FPS,
        transition_lambda=lam, num_threads=2,
    )  # fmt: skip


def spread(tracked) -> float:
    downs = tracked[tracked[:, 1] == 1][:, 0]
    lengths = np.diff(downs)
    return float(np.std(lengths) / np.median(lengths)) if len(lengths) > 2 else 0.0


def activations(path: Path, model) -> np.ndarray:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(path, wav)
        return beat_this.dbn_activations(*model.logits(read_wav(wav)))


def main():
    sys.stdout.reconfigure(errors="replace")
    model = beat_this.BeatThisModel(threads=4)
    dbns = {lam: dbn(lam) for lam in LAMBDAS}
    print("bar-length spread (lower = steadier bars):")
    print(f"{'song':26}" + "".join(f"{f'lambda {lam}':>13}" for lam in LAMBDAS))
    for name, key in UNEVEN.items():
        acts = activations(DOWNLOADS / f"{key}.webm", model)
        cells = [spread(np.asarray(dbns[lam](acts)).reshape(-1, 2)) for lam in LAMBDAS]
        print(f"{name:26}" + "".join(f"{c:13.0%}" for c in cells), flush=True)

    results = sorted((ROOT / "evaluate" / "results").glob("*.json"))[-1]
    offsets = {slug: r["offset"] for slug, r in json.loads(results.read_text())["songs"].items()}
    totals = {lam: [] for lam in LAMBDAS}
    for song in data.load_songs():
        ref_int, ref_lab = data.read_chords(song.chord_file)
        ref_beats, ref_pos = data.read_beats(song.beat_file)
        path = resolve_source(song.link).path
        acts = activations(path, model)
        duration = len(acts) / beat_this.FPS
        _, _, beats, positions = score.shift_reference(
            ref_int, ref_lab, ref_beats, ref_pos, offsets[song.slug], duration
        )
        for lam in LAMBDAS:
            tracked = np.asarray(dbns[lam](acts)).reshape(-1, 2)
            s = score.beat_scores(beats, positions, _beats(tracked))
            totals[lam].append((ref_int[-1, 1], s))
    print("\nannotated Beatles songs, duration-weighted means:")
    for lam in LAMBDAS:
        weights = [w for w, _ in totals[lam]]
        mean = {
            m: score.weighted_mean([s[m] for _, s in totals[lam]], weights)
            for m in ("beat_f", "cmlt", "downbeat_f")
        }
        meters = sum(s["meter_est"] == s["meter_ref"] for _, s in totals[lam])
        print(f"lambda {lam:5}: beat F {mean['beat_f']:.3f}  CMLt {mean['cmlt']:.3f}  "
              f"downbeat F {mean['downbeat_f']:.3f}  meter {meters}/{len(weights)}")  # fmt: skip


if __name__ == "__main__":
    main()
