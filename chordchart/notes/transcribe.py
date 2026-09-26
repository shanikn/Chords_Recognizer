"""Step 3 of notes: one stem -> note events, with Spotify's basic-pitch.

basic-pitch is a small polyphonic model that works on any instrument. Its ONNX copy
runs through onnxruntime (see pyproject.toml for why not tensorflow). It returns note
events (start, end, MIDI pitch, amplitude); the quantizer does the rest.

Settings that differ from basic-pitch's defaults:
- minimum note length 70 ms instead of 128 ms. The default would drop real 16th notes
  above ~117 BPM; quantize.py filters on the song's own grid instead.
- for the bass stem, nothing above 500 Hz: what basic-pitch finds up there is the
  bass's own overtones, heard as extra notes an octave or more higher.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from chordchart.notes.model import Note

ONSET_THRESHOLD = 0.5  # basic-pitch's defaults
FRAME_THRESHOLD = 0.3
MIN_NOTE_SECONDS = 0.07
FREQUENCY_LIMITS: dict[str, tuple[float | None, float | None]] = {"bass": (None, 500.0)}

_model = None
_model_lock = threading.Lock()


def transcribe(path: Path, instrument: str, offset: float = 0.0) -> list[Note]:
    """Notes in the audio file at `path`, with `offset` added to every time."""
    model = _load_model()  # first: it imports basic-pitch quietly
    from basic_pitch.constants import AUDIO_SAMPLE_RATE, FFT_HOP
    from basic_pitch.inference import run_inference
    from basic_pitch.note_creation import model_output_to_notes

    output = run_inference(path, model)
    low, high = FREQUENCY_LIMITS.get(instrument, (None, None))
    _, events = model_output_to_notes(
        output,
        onset_thresh=ONSET_THRESHOLD,
        frame_thresh=FRAME_THRESHOLD,
        min_note_len=round(MIN_NOTE_SECONDS * AUDIO_SAMPLE_RATE / FFT_HOP),
        min_freq=low,
        max_freq=high,
        include_pitch_bends=False,
    )
    notes = [
        Note(
            start=round(float(start) + offset, 4),
            end=round(float(end) + offset, 4),
            pitch=int(pitch),
            velocity=min(127, max(1, round(127 * float(amplitude)))),
        )
        for start, end, pitch, amplitude, _bends in events
    ]
    return sorted(notes, key=lambda n: (n.start, n.pitch))


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            # basic-pitch logs a warning at import for each model runtime that isn't
            # installed (tensorflow, CoreML, TFLite); only ONNX is, on purpose.
            root = logging.getLogger()
            level = root.level
            root.setLevel(logging.ERROR)
            try:
                from basic_pitch import ICASSP_2022_MODEL_PATH
                from basic_pitch.inference import Model
            finally:
                root.setLevel(level)
            _model = Model(ICASSP_2022_MODEL_PATH)
        return _model
