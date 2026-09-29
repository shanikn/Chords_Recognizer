"""Which analysis stage leaves memory behind? Runs each stage on its own, in order, in
one process, and prints the app's working set / private bytes after each.

    uv run --with psutil python experiments/profile/stage_memory.py [--no-arena]
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import DOWNLOADS  # noqa: E402

SONG = DOWNLOADS / "youtube-20iOlPwz0J0.webm"


def mem(label):
    import psutil

    info = psutil.Process().memory_info()
    print(f"{label:28} rss {info.rss >> 20:5} MB  private {info.private >> 20:5} MB", flush=True)


def main():
    from chordchart import beat_this
    from chordchart.beats import _samples
    from chordchart.fetch import decode_section, load_signal
    from chordchart.key import detect_key
    from chordchart.processors import Processors
    from chordchart.recognizers.madmom_crf import MadmomCRFRecognizer

    procs = Processors(num_threads=4)
    if "--no-arena" in sys.argv:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.enable_cpu_mem_arena = False
        procs.beat_this.session = ort.InferenceSession(
            str(beat_this.MODEL), options, providers=["CPUExecutionProvider"]
        )
    mem("models loaded")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        decode_section(SONG, wav)
        audio = load_signal(wav)
        mem("decoded")
        logits = procs.beat_this.logits(_samples(audio))
        mem("Beat This! logits")
        procs.beat_this_dbn(beat_this.dbn_activations(*logits))
        mem("DBN")
        MadmomCRFRecognizer(procs).recognize(audio)
        mem("chords")
        detect_key(audio, procs)
        mem("key")
        del audio, logits
    import gc

    gc.collect()
    mem("released audio + gc")
    procs.close()


if __name__ == "__main__":
    main()
