"""Golden files: every stage of the Python pipeline, per song, for the C++ core's tests.

    uv run python android/tools/make_golden.py [SONG_FILE ...]    (default: every cached song)

Writes android/golden/<song>/ (git-ignored: the songs are copyrighted):

    pcm.npy               int16, 44.1 kHz mono: the decoded audio every stage starts from
    chord_spec.npy        float32 (T, 113): madmom's log-filtered spectrogram at 10 fps
    key_spec.npy          float32 (T, 105): the same at 5 fps
    chord_features.npy    float32 (T, 128): the chord CNN's output (desktop pipeline)
    crf_path.npy          uint32 (T,): the CRF's best class per frame
    key_probs.npy         float32 (24,)
    bt_resampled.npy      float32: the audio resampled to 22.05 kHz (soxr HQ)
    bt_mel.npy            float32 (F, 128): Beat This!'s log-mel spectrogram
    bt_beat_logits.npy, bt_down_logits.npy   float32 (F,)
    dbn_activations.npy   float64 (F, 2): what madmom's DBN gets
    dbn{3,4}_path.npy     uint32: each meter's Viterbi path (after madmom's thresholding)
    tracked.npy           float64 (N, 2): beat times and positions (the DBN's result)
    stages.json           the rest: DBN threshold offset and log-probabilities, chord
                          segments, key, and the whole Song (as `chordchart <file> --format
                          json` gives it, with the debug stages)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
GOLDEN = HERE.parent / "golden"
sys.path.insert(0, str(HERE.parents[1] / "experiments"))
from common import DOWNLOADS  # noqa: E402


def golden(song: Path, procs) -> dict:
    import soxr
    from madmom.features.beats import threshold_activations
    from madmom.features.key import key_prediction_to_label

    from chordchart import beat_this as bt
    from chordchart.beats import _samples
    from chordchart.fetch import decode_section, load_signal
    from chordchart.pipeline import analyze

    out = GOLDEN / song.stem
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(song, wav)
        audio = load_signal(wav)
    np.save(out / "pcm.npy", np.asarray(audio, dtype=np.int16))

    stages = {}
    # chords and key (madmom spectrograms, CNNs as the desktop pipeline runs them, CRF)
    for name, processor in (("chord", procs.chord_features), ("key", procs.key)):
        spec = audio
        for step in processor.processors[:4]:
            spec = step(spec)
        np.save(out / f"{name}_spec.npy", np.asarray(spec, dtype=np.float32))
        result = spec
        for step in processor.processors[4:]:
            result = step(result)
        if name == "chord":
            np.save(out / "chord_features.npy", np.asarray(result, dtype=np.float32))
            features = result
        else:
            np.save(out / "key_probs.npy", np.asarray(result, dtype=np.float32).reshape(-1))
            stages["key"] = key_prediction_to_label(result)
    crf, labels = procs.chord_crf.processors
    path = crf(features)
    np.save(out / "crf_path.npy", np.asarray(path, dtype=np.uint32))
    stages["segments"] = [[float(s), float(e), str(lab)] for s, e, lab in labels(path)]

    # beats: Beat This! frontend, model, DBN
    samples = _samples(audio)
    resampled = soxr.resample(samples, in_rate=44100, out_rate=bt.SR)
    np.save(out / "bt_resampled.npy", np.asarray(resampled, dtype=np.float32))
    np.save(out / "bt_mel.npy", bt.log_mel(resampled))
    beat, down = procs.beat_this.logits(samples)
    np.save(out / "bt_beat_logits.npy", beat)
    np.save(out / "bt_down_logits.npy", down)
    activations = bt.dbn_activations(beat, down)
    np.save(out / "dbn_activations.npy", activations)
    dbn = procs.beat_this_dbn
    thresholded, first = threshold_activations(activations, dbn.threshold)
    stages["dbn_first"] = int(first)
    stages["dbn_log_probabilities"] = {}
    for meter, hmm in zip(dbn.beats_per_bar, dbn.hmms, strict=True):
        path, log_p = hmm.viterbi(thresholded)
        np.save(out / f"dbn{int(meter)}_path.npy", np.asarray(path, dtype=np.uint32))
        stages["dbn_log_probabilities"][str(int(meter))] = float(log_p)
    np.save(out / "tracked.npy", np.asarray(dbn(activations), dtype=np.float64).reshape(-1, 2))

    # the whole analysis, as the app would print it
    song = analyze(song, processors=procs, cache=False)
    stages["song"] = json.loads(song.to_json(include_debug=True))
    (out / "stages.json").write_text(json.dumps(stages, indent=1), encoding="utf-8")
    return {"song": song.title, "bars": len(song.bars), "key": stages["key"], "frames": len(beat)}


def main() -> int:
    from chordchart.processors import Processors

    songs = [Path(a) for a in sys.argv[1:]] or sorted(
        p for p in DOWNLOADS.iterdir() if p.suffix in {".webm", ".m4a", ".mp3", ".opus"}
    )
    with Processors(num_threads=4) as procs:
        for song in songs:
            info = golden(song, procs)
            print(f"{song.name}: {info}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
