"""Notes: the stem cache (fast, Demucs faked) and the real models on generated audio (slow).

The slow tests use the real Demucs weights in %LOCALAPPDATA%\\ChordChart\\models, so
the first run downloads them (53 MB).
"""

import numpy as np
import pytest
import soundfile
from scipy.io import wavfile

from chordchart.notes import stems as stems_mod
from chordchart.notes.model import INSTRUMENTS
from chordchart.notes.stems import STEM_RATE, decode_stereo, separate
from chordchart.notes.transcribe import transcribe

SR = 44_100


@pytest.fixture
def song(make_audio):
    # Left: 220 Hz, right: 330 Hz, so a stereo decode can be told from a mono one.
    return make_audio("stereo", "sine=f=220:d=8[l];sine=f=330:d=8[r];[l][r]amerge", 8, "wav")


def test_decode_stereo_keeps_both_channels(song):
    mix = decode_stereo(song, offset=1.0, duration=2.0)
    assert mix.shape == (2, 2 * SR)
    assert mix.dtype == np.float32
    spectrum = [np.abs(np.fft.rfft(ch)) for ch in mix]
    freqs = np.fft.rfftfreq(mix.shape[1], 1 / SR)
    assert [freqs[np.argmax(s)] for s in spectrum] == pytest.approx([220, 330], abs=1)


def _fake_demucs(monkeypatch):
    calls = []

    def run(model, mix, progress=None):
        calls.append(mix.shape)
        if progress:
            progress(1.0)
        loud = {"piano": 0.5, "bass": 0.1, "guitar": 0.01, "other": 0.0}
        n = mix.shape[1]
        stems = {k: v * np.sin(np.arange(n) / 10).astype(np.float32) for k, v in loud.items()}
        return {**stems, "vocals": mix[0], "drums": mix[1]}

    monkeypatch.setattr(stems_mod, "load_model", lambda status=None: object())
    monkeypatch.setattr(stems_mod, "run_demucs", run)
    return calls


def test_stems_are_cached_as_22khz_flac(song, tmp_path, monkeypatch):
    calls = _fake_demucs(monkeypatch)
    seen = []
    first = separate(song, 0.0, 8.0, tmp_path, progress=seen.append)
    assert seen == [1.0]
    assert set(first.levels) == set(INSTRUMENTS)  # vocals and drums are discarded
    assert max(first.levels, key=first.levels.get) == "piano"
    audio, rate = soundfile.read(first.path("piano"))
    assert rate == STEM_RATE and len(audio) == pytest.approx(8 * STEM_RATE, abs=10)

    messages = []
    second = separate(song, 0.0, 8.0, tmp_path, status=messages.append)
    assert len(calls) == 1  # Demucs ran once
    assert second == first
    assert messages == ["using cached instrument stems"]

    separate(song, 0.0, 8.0, tmp_path, refresh=True)
    assert len(calls) == 2


def test_another_section_is_another_cache_entry(song, tmp_path, monkeypatch):
    calls = _fake_demucs(monkeypatch)
    separate(song, 0.0, 8.0, tmp_path)
    separate(song, 1.0, 5.0, tmp_path)
    assert len(calls) == 2
    assert stems_mod.summary(tmp_path)[0] == 2
    assert stems_mod.clear(tmp_path)[0] == 2
    assert stems_mod.summary(tmp_path) == (0, 0)


def test_an_incomplete_cache_entry_is_a_miss(song, tmp_path, monkeypatch):
    calls = _fake_demucs(monkeypatch)
    stems = separate(song, 0.0, 8.0, tmp_path)
    stems.path("bass").unlink()
    separate(song, 0.0, 8.0, tmp_path)
    assert len(calls) == 2


@pytest.mark.slow
def test_demucs_separates_a_real_mix(song, tmp_path):
    progress = []
    stems = separate(song, 0.0, 8.0, tmp_path, progress=progress.append)
    assert set(stems.levels) == set(INSTRUMENTS)
    assert progress and progress[-1] == pytest.approx(1.0)
    assert progress == sorted(progress)


def _tones(path, pitches, seconds=0.5, gap=0.1):
    """A plucked-string-like tone per pitch (harmonics that decay), one after another."""
    t = np.arange(int(seconds * SR)) / SR
    parts = []
    for pitch in pitches:
        f = 440 * 2 ** ((pitch - 69) / 12)
        tone = sum(0.5**k * np.sin(2 * np.pi * f * (k + 1) * t) for k in range(4))
        parts += [tone * np.exp(-3 * t), np.zeros(int(gap * SR))]
    x = np.concatenate(parts)
    wavfile.write(path, SR, (0.5 * x / np.abs(x).max() * 32767).astype(np.int16))
    return path


@pytest.mark.slow
def test_basic_pitch_finds_the_played_pitches(tmp_path):
    melody = [57, 60, 64, 67, 72]
    notes = transcribe(_tones(tmp_path / "m.wav", melody), "piano", offset=10.0)
    # The strongest note starting near each tone's onset is the pitch played.
    found = []
    for i in range(len(melody)):
        onset = 10.0 + i * 0.6
        near = [n for n in notes if abs(n.start - onset) < 0.1]
        found.append(max(near, key=lambda n: n.velocity).pitch if near else None)
    assert found == melody


def test_separation_is_seeded_and_leaves_the_global_generators_alone():
    import random

    import torch

    from chordchart.notes.stems import _seeded

    random.seed(123)
    expected_outer = random.random()
    random.seed(123)
    with _seeded(0):
        first = (random.randint(0, 22050), torch.rand(1).item())
    with _seeded(0):
        second = (random.randint(0, 22050), torch.rand(1).item())
    assert first == second  # Demucs's random shift is the same on every run
    assert random.random() == expected_outer  # the caller's random state is restored
