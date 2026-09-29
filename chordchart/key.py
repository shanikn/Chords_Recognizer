"""Global key detection with madmom's key CNN (Korzeniowski & Widmer, 2018).

The network looks at the whole track and outputs a probability for each of the 24
major/minor keys. We report the most likely one and its probability, so a low
confidence (e.g. a modal or key-changing song) can be flagged to the user.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from chordchart.fetch import model_input
from chordchart.model import Key

if TYPE_CHECKING:
    from chordchart.processors import Processors


def detect_key(wav_path: Path, processors: Processors | None = None) -> Key:
    from madmom.features.key import CNNKeyRecognitionProcessor, key_prediction_to_label

    from chordchart.fastconv import accelerated

    key_cnn = processors.key if processors is not None else accelerated(CNNKeyRecognitionProcessor())
    prediction = key_cnn(model_input(wav_path))
    tonic, mode = key_prediction_to_label(prediction).split()  # "G# minor"
    return Key(tonic=tonic, mode=mode, confidence=float(np.max(prediction)))
