"""Default chord recognizer: madmom's CNN + CRF (Korzeniowski & Widmer, 2016,
"A fully convolutional deep auditory model for musical chord recognition").

**Chroma, the classic idea.** A chroma vector has 12 numbers, one per pitch class
(C, C#, ..., B): how much energy the audio has in that pitch class, summed over every
octave. A C major chord lights up C, E and G whatever the voicing or octave, which makes
chroma the traditional feature for chord recognition. Hand-built chroma (a filter bank
folded into 12 bins) also picks up overtones, drums and vocals, which blurs the picture.
madmom also has a *learned* 12-bin chroma (`DeepChromaProcessor`), used in milestone 7.

**What this recognizer uses instead (CNNChordFeatureProcessor).** A convolutional
network trained on songs with annotated chords reads a short spectrogram context
around each frame and outputs 128 learned features per frame (10 frames per second).
It skips the 12-bin chroma bottleneck entirely: the network decides for itself which
spectral patterns matter for harmony, and it learns to ignore most of the percussion
and melody. You can think of the features as a richer, learned cousin of chroma.

**CRF (CRFChordRecognitionProcessor).** Classifying each frame on its own would flicker
(C, C, Am, C, C...) wherever the evidence is ambiguous. A linear-chain conditional
random field scores whole label *sequences*: per-frame evidence plus a learned cost for
switching chords. Viterbi decoding returns the best sequence overall, so a brief
contradictory frame loses to a stable chord. Vocabulary: 12 major + 12 minor + N (no
chord) = 25 labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from chordchart.fetch import model_input
from chordchart.model import Segment

if TYPE_CHECKING:
    from chordchart.processors import Processors


class MadmomCRFRecognizer:
    name = "madmom-crf"
    accepts_signal = True  # recognize() also takes an in-memory madmom Signal

    def __init__(self, processors: Processors | None = None) -> None:
        self.processors = processors  # reused if given; otherwise built per call

    def recognize(self, wav_path: Path) -> list[Segment]:
        if self.processors is not None:
            cnn, crf = self.processors.chord_features, self.processors.chord_crf
        else:
            from madmom.features.chords import (
                CNNChordFeatureProcessor,
                CRFChordRecognitionProcessor,
            )

            from chordchart.fastconv import accelerated

            cnn, crf = accelerated(CNNChordFeatureProcessor()), CRFChordRecognitionProcessor()
        rows = crf(cnn(model_input(wav_path)))
        return [
            Segment(float(start), float(end), str(label))
            for start, end, label in zip(rows["start"], rows["end"], rows["label"], strict=True)
        ]
