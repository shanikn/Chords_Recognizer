"""Step 1 of notes: split the song into instrument stems with Demucs (htdemucs_6s).

htdemucs_6s separates drums, bass, other, vocals, guitar and piano. Vocals and drums
are discarded (see model.INSTRUMENTS). The other four are what the main instrument is
chosen from.

Demucs wants stereo 44.1 kHz, so the chart's range of the source is decoded again
here, in stereo (the chord pipeline's WAV is mono). It runs on the CPU at about twice
the audio's length, so the result is cached: the four stems as mono 22.05 kHz FLAC in
<cache root>/stems/<key>/, keyed by the stereo audio. 22.05 kHz is what basic-pitch
works at anyway, so nothing it needs is lost. `levels.json` is written last and marks a
complete entry.

The weights (53 MB, MIT licence) are downloaded from the Hugging Face hub on first use,
at a pinned revision, into %LOCALAPPDATA%\\ChordChart\\models, outside the cache so
`chordchart cache clear` doesn't throw them away.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from chordchart import bundled
from chordchart.errors import AudioDecodeError, NetworkError
from chordchart.fetch import SAMPLE_RATE, find_ffmpeg
from chordchart.notes.model import INSTRUMENTS
from chordchart.notes.select import stem_levels

MODEL_NAME = "htdemucs_6s"
MODEL_REPO = "adefossez/HTDemucs-6s"
MODEL_REVISION = "3c5ee475be622df764938de97e4281a7b07ffa58"
MODEL_MB = 53
STEM_RATE = 22_050
CACHE_VERSION = 1

Progress = Callable[[float], None]  # fraction done, 0..1
Status = Callable[..., None]

_ENTRY = re.compile(r"^[0-9a-f]{64}$")
_model = None
_model_lock = threading.Lock()


@dataclass(frozen=True)
class Stems:
    folder: Path
    levels: dict[str, float]  # dBFS per instrument

    def path(self, instrument: str) -> Path:
        return self.folder / f"{instrument}.flac"


def models_dir() -> Path:
    return bundled.app_data_dir() / "models"


def separate(
    src: Path,
    offset: float,
    duration: float,
    cache_folder: Path,
    *,
    refresh: bool = False,
    status: Status | None = None,
    progress: Progress | None = None,
) -> Stems:
    """The four candidate stems of `src` from `offset` for `duration` seconds."""
    mix = decode_stereo(src, offset, duration)
    key = _cache_key(mix)
    folder = cache_folder / key
    if not refresh and (cached := _load(folder)) is not None:
        if status:
            status("using cached instrument stems")
        return cached

    model = load_model(status)
    separated = run_demucs(model, mix, progress)  # name -> mono float32 at 44.1 kHz
    kept = {name: separated[name] for name in INSTRUMENTS}
    return _store(folder, kept)


def decode_stereo(src: Path, offset: float, duration: float) -> np.ndarray:
    """(2, samples) float32 at 44.1 kHz, straight from ffmpeg (no temporary file)."""
    cmd = [find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error"]
    if offset > 0:
        cmd += ["-ss", f"{offset:.3f}"]
    cmd += ["-t", f"{duration:.3f}", "-i", str(src), "-vn", "-ac", "2", "-ar", str(SAMPLE_RATE)]
    cmd += ["-f", "f32le", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip()
        raise AudioDecodeError(f"ffmpeg could not decode {Path(src).name}: {message}")
    samples = np.frombuffer(result.stdout, dtype="<f4")
    return samples[: len(samples) // 2 * 2].reshape(-1, 2).T.copy()


def load_model(status: Status | None = None):
    """The Demucs model, loaded once per process (and downloaded once per computer)."""
    global _model
    with _model_lock:
        if _model is None:
            _model = _load_model(status)
        return _model


def _load_model(status: Status | None):
    import yaml
    from demucs.apply import BagOfModels
    from demucs.hf import load_safetensors_model

    with open(_weights_file(f"{MODEL_NAME}.yaml", status)) as f:
        bag = yaml.safe_load(f)
    models = [
        load_safetensors_model(_weights_file(f"{sig}.safetensors", status)) for sig in bag["models"]
    ]
    model = BagOfModels(models, bag.get("weights"), bag.get("segment"))
    model.eval()
    return model


def _weights_file(filename: str, status: Status | None) -> str:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    args = dict(repo_id=MODEL_REPO, filename=filename, revision=MODEL_REVISION)
    try:
        return hf_hub_download(**args, cache_dir=models_dir(), local_files_only=True)
    except LocalEntryNotFoundError:
        pass
    if status and filename.endswith(".safetensors"):
        status(f"downloading the instrument separation model ({MODEL_MB} MB, first time only)")
    # It warns that the request is unauthenticated (fine for a public model), and the
    # HTTP client logs every request, signed CDN links included: keep the log quiet.
    logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return hf_hub_download(**args, cache_dir=models_dir())
    except Exception as exc:  # no network, proxy, Hugging Face down: one line for the user
        raise NetworkError(
            f"could not download the instrument separation model: {type(exc).__name__}: {exc}"
        ) from exc


def run_demucs(model, mix: np.ndarray, progress: Progress | None = None) -> dict[str, np.ndarray]:
    """Separate `mix` (2, samples); return each source as mono float32.

    The input is normalised the way Demucs's own command line does it (zero mean,
    unit variance of the mono mix), and the output is scaled back.
    """
    import torch
    from demucs.apply import apply_model

    wav = torch.from_numpy(mix)
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    length = wav.shape[-1]
    # Chunks are cut per model in the bag, at that model's segment length.
    segment = int(float(getattr(model, "models", [model])[0].segment) * model.samplerate)

    def on_chunk(info: dict) -> None:
        if progress and info.get("state") == "end":
            progress(min(1.0, (info["segment_offset"] + segment) / length))

    with torch.no_grad():
        out = apply_model(
            model, ((wav - mean) / std)[None], split=True, overlap=0.25, callback=on_chunk
        )[0]
    out = out * std + mean
    return {name: out[i].mean(0).numpy() for i, name in enumerate(model.sources)}


def _cache_key(mix: np.ndarray) -> str:
    digest = hashlib.sha256(mix.tobytes())
    digest.update(f"|v{CACHE_VERSION}|{MODEL_NAME}@{MODEL_REVISION}".encode())
    return digest.hexdigest()


def _load(folder: Path) -> Stems | None:
    try:
        levels = json.loads((folder / "levels.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    stems = Stems(folder, levels)
    ok = set(levels) == set(INSTRUMENTS) and all(stems.path(n).is_file() for n in INSTRUMENTS)
    return stems if ok else None


def _store(folder: Path, stems: dict[str, np.ndarray]) -> Stems:
    import soundfile
    import soxr

    levels = stem_levels(stems)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "levels.json").unlink(missing_ok=True)
    for name, audio in stems.items():
        low = soxr.resample(audio, SAMPLE_RATE, STEM_RATE)
        soundfile.write(folder / f"{name}.flac", np.clip(low, -1, 1), STEM_RATE, subtype="PCM_16")
    (folder / "levels.json").write_text(json.dumps(levels), encoding="utf-8")
    return Stems(folder, levels)


def summary(folder: Path) -> tuple[int, int]:
    """(number of cached songs, bytes used)."""
    entries = _entries(folder)
    size = sum(p.stat().st_size for e in entries for p in e.rglob("*") if p.is_file())
    return len(entries), size


def clear(folder: Path) -> tuple[int, int]:
    """Delete every cached entry; return what was removed, like summary()."""
    removed = summary(folder)
    for entry in _entries(folder):
        shutil.rmtree(entry, ignore_errors=True)
    return removed


def _entries(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in folder.iterdir() if p.is_dir() and _ENTRY.match(p.name)]
