"""Global key detection with madmom's key CNN (Korzeniowski & Widmer, 2018).

The network looks at the whole track and outputs a probability for each of the 24
major/minor keys. We report the most likely one and its probability, so a low
confidence (e.g. a modal or key-changing song) can be flagged to the user.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from chordchart.model import Key


def detect_key(wav_path: Path) -> Key:
    from madmom.features.key import CNNKeyRecognitionProcessor, key_prediction_to_label

    prediction = CNNKeyRecognitionProcessor()(str(wav_path))
    tonic, mode = key_prediction_to_label(prediction).split()  # "G# minor"
    return Key(tonic=tonic, mode=mode, confidence=float(np.max(prediction)))
