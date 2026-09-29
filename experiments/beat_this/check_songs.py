"""Beat This! + DBN (the default) and madmom on every cached song, with obvious-failure
flags: tempo doubled or halved against madmom, a different meter, a 2-beat meter (maybe
6/8 in dotted quarters), a tempo outside 60-200 BPM, or bars of uneven length.
Flags are for a human to listen to, not verdicts: madmom isn't ground truth.

    uv run python experiments/beat_this/check_songs.py
"""

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import cached_songs  # noqa: E402

from chordchart.beats import track_beats, track_beats_madmom  # noqa: E402
from chordchart.fetch import decode_section, load_signal  # noqa: E402
from chordchart.pipeline import DEFAULT_THREADS  # noqa: E402
from chordchart.processors import Processors  # noqa: E402


def bar_spread(beats) -> float:
    """Relative spread of bar lengths (std / median); large = bars of uneven length."""
    downbeats = [t for t, p in zip(beats.times, beats.positions, strict=True) if p == 1]
    lengths = np.diff(downbeats)
    return float(np.std(lengths) / np.median(lengths)) if len(lengths) > 2 else 0.0


def flags(new, old) -> list[str]:
    out = []
    ratio = new.bpm / old.bpm if old.bpm else 0
    if abs(ratio - 2) < 0.16:
        out.append("tempo x2 vs madmom")
    elif abs(ratio - 0.5) < 0.04:
        out.append("tempo /2 vs madmom")
    if new.meter != old.meter:
        out.append(f"meter {new.meter} vs madmom {old.meter}")
    if new.meter == 2:
        out.append("2 beats per bar")
    if not 60 <= new.bpm <= 200:
        out.append(f"tempo {new.bpm:.0f} BPM")
    if bar_spread(new) > 0.1:
        out.append(f"uneven bars ({bar_spread(new):.0%})")
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # titles in any script on a cp1252 console
    rows = []
    with Processors(num_threads=DEFAULT_THREADS) as processors:
        for src in cached_songs():
            meta = src.with_suffix(".json")
            title = (
                json.loads(meta.read_text(encoding="utf-8")).get("title")
                if meta.exists()
                else src.stem
            )
            with tempfile.TemporaryDirectory() as tmp:
                wav = Path(tmp) / "a.wav"
                decoded = decode_section(src, wav)
                audio = load_signal(wav)
                new = track_beats(audio, (3, 4), processors)
                old = track_beats_madmom(audio, (3, 4), processors)
            rows.append((title, decoded.duration, new, old, flags(new, old)))
            verdict = "; ".join(flags(new, old)) or "ok"
            print(
                f"{title[:44]:44} {decoded.duration:4.0f}s  "
                f"beat-this {new.bpm:5.1f} BPM {new.meter}/4  "
                f"madmom {old.bpm:5.1f} BPM {old.meter}/4  {verdict}",
                flush=True,
            )
    flagged = [r for r in rows if r[4]]
    print(f"\n{len(flagged)} of {len(rows)} songs flagged")


if __name__ == "__main__":
    main()
