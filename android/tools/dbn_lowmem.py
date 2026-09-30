"""Prototype of the phone's low-memory Viterbi for madmom's DBN, checked against madmom.

madmom's HiddenMarkovModel.viterbi keeps a uint32 back-pointer per frame and state: for
the 4/4 bar model (5796 states at Beat This!'s 50 fps) that is 265 MB for a 4-minute
song, 199 MB more for 3/4. Two changes, both exact:

1. Each state has at most 12 predecessors, so a back-pointer is stored as the 1-byte
   index of the winning predecessor within the state's list (4x less).
2. Checkpoints: only every K-th frame's Viterbi scores are kept while going forward
   (float64, one vector per checkpoint). Backtracking recomputes one K-frame segment at
   a time from its checkpoint, holding just that segment's back-pointers. Memory is
   ~(frames / K) x states x 8 + K x states bytes; the forward pass runs twice.

The arithmetic is madmom's, in the same order: score = previous[prev] + transition +
density (float64), and a later predecessor wins only if strictly greater, so ties keep
the first one. Hence the path is identical, not just close.

    uv run python android/tools/dbn_lowmem.py [SONG ...]
"""

from __future__ import annotations

import math
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "experiments"))
from common import DOWNLOADS  # noqa: E402


class Model:
    """The arrays of one madmom bar HMM, for the step below."""

    def __init__(self, hmm, activations):
        tm, om = hmm.transition_model, hmm.observation_model
        self.states = np.asarray(tm.states, dtype=np.int64)  # predecessor of each transition
        self.pointers = np.asarray(tm.pointers, dtype=np.int64)  # per state: its transitions
        self.log_probs = np.asarray(tm.log_probabilities, dtype=np.float64)
        self.initial = np.log(np.asarray(hmm.initial_distribution, dtype=np.float64))
        self.densities = np.asarray(om.log_densities(activations), dtype=np.float64)
        self.om_pointers = np.asarray(om.pointers, dtype=np.int64)
        self.num_states = len(self.pointers) - 1
        counts = np.diff(self.pointers)
        assert counts.max() < 256, "a predecessor index must fit a byte"
        self.target = np.repeat(np.arange(self.num_states), counts)  # state of each transition
        self.offset = (
            np.arange(len(self.states)) - self.pointers[self.target]
        )  # index within its state
        self.starts = self.pointers[:-1]

    def step(self, previous, frame):
        """One Viterbi frame: new scores, and each state's winning predecessor index."""
        density = self.densities[frame, self.om_pointers]
        # madmom's order of additions: previous + transition + density
        scores = previous[self.states] + self.log_probs + density[self.target]
        best = np.maximum.reduceat(scores, self.starts)
        # first transition reaching the max (madmom keeps the first on ties: strict >)
        is_best = scores == best[self.target]
        big = np.iinfo(np.int64).max
        first = np.minimum.reduceat(np.where(is_best, self.offset, big), self.starts)
        # all -inf: madmom leaves the pointer unset; such a state never ends a finite path
        first = np.where(first == big, 0, first).astype(np.uint8)
        return best, first


def viterbi_lowmem(model: Model, checkpoint_every: int):
    frames = len(model.densities)
    k = checkpoint_every
    checkpoints = []
    previous = model.initial
    for frame in range(frames):
        if frame % k == 0:
            checkpoints.append(previous.copy())
        previous, _ = model.step(previous, frame)
    state = int(np.argmax(previous))
    log_probability = float(previous[state])
    if math.isinf(log_probability):
        return np.empty(0, dtype=np.uint32), log_probability
    path = np.empty(frames, dtype=np.uint32)
    peak_segment = 0
    for segment in range(len(checkpoints) - 1, -1, -1):
        begin, end = segment * k, min((segment + 1) * k, frames)
        indices = np.empty((end - begin, model.num_states), dtype=np.uint8)
        scores = checkpoints[segment]
        for frame in range(begin, end):
            scores, indices[frame - begin] = model.step(scores, frame)
        peak_segment = max(peak_segment, indices.nbytes)
        for frame in range(end - 1, begin - 1, -1):
            path[frame] = state
            state = int(model.states[model.pointers[state] + indices[frame - begin, state]])
    memory = {
        "checkpoints_mb": sum(c.nbytes for c in checkpoints) / 2**20,
        "segment_mb": peak_segment / 2**20,
        "madmom_mb": frames * model.num_states * 4 / 2**20,
    }
    return path, log_probability, memory


def main() -> int:
    from chordchart import beat_this
    from chordchart.beats import _samples
    from chordchart.fetch import decode_section, load_signal

    names = sys.argv[1:] or ["youtube-NrgmdOz227I.webm", "youtube-20iOlPwz0J0.webm"]
    model_bt = beat_this.BeatThisModel(threads=4)
    dbn = beat_this.make_dbn((3, 4))
    ok = True
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            decode_section(DOWNLOADS / name, wav)
            samples = _samples(load_signal(wav))
        activations = beat_this.dbn_activations(*model_bt.logits(samples))
        seconds = len(activations) / beat_this.FPS
        for bpb, hmm in zip((3, 4), dbn.hmms, strict=True):
            began = time.perf_counter()
            ref_path, ref_logp = hmm.viterbi(activations)
            madmom_s = time.perf_counter() - began
            model = Model(hmm, activations)
            k = max(1, int(math.sqrt(len(activations))))  # ~sqrt(frames): memory balance
            began = time.perf_counter()
            path, logp, memory = viterbi_lowmem(model, k)
            low_s = time.perf_counter() - began
            same = np.array_equal(path, ref_path) and logp == ref_logp
            ok &= same
            print(
                f"{name} ({seconds:.0f} s) {bpb}/4: path identical: {same}; "
                f"back-pointers madmom {memory['madmom_mb']:.0f} MB -> "
                f"checkpoints {memory['checkpoints_mb']:.1f} MB + "
                f"segment {memory['segment_mb']:.2f} MB "
                f"(K={k}); madmom {madmom_s:.1f} s, numpy prototype {low_s:.1f} s",
                flush=True,
            )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
