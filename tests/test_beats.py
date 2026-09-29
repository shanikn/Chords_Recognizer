import pytest

from chordchart.beats import bpm_from_times, meter_from_positions, track_beats, track_beats_madmom


def test_bpm_uses_median_interval():
    # One outlier interval (a tracking glitch) must not move the tempo.
    times = [0.0, 0.5, 1.0, 1.5, 2.6, 3.1, 3.6]
    assert bpm_from_times(times) == pytest.approx(120.0)


def test_bpm_needs_two_beats():
    assert bpm_from_times([1.0]) == 0.0


def test_meter_is_most_common_complete_bar_length():
    positions = [3, 4, 1, 2, 3, 4, 1, 2, 3, 4, 1, 2, 3, 1, 2, 3, 4, 1, 2]
    assert meter_from_positions(positions) == 4


def test_meter_without_complete_bar_falls_back_to_max_position():
    assert meter_from_positions([1, 2, 3]) == 3


@pytest.mark.slow
@pytest.mark.parametrize("tracker", [track_beats, track_beats_madmom], ids=["beat_this", "madmom"])
def test_tracks_click_track(click_track, tracker):
    beats = tracker(click_track)

    assert beats.bpm == pytest.approx(120, abs=2)
    assert beats.meter == 4
    downbeats = [t for t, p in zip(beats.times, beats.positions, strict=True) if p == 1]
    assert len(downbeats) >= 8
    # The accented clicks are at 0, 2, 4, ... s. The tracker must find that phase,
    # not just the tempo.
    assert all(abs(t / 2 - round(t / 2)) < 0.05 for t in downbeats)


def test_the_bundled_model_is_the_verified_one():
    import hashlib

    from chordchart import beat_this

    assert hashlib.sha256(beat_this.MODEL.read_bytes()).hexdigest() == beat_this.MODEL_SHA256


def test_beat_this_frontend_and_model_shapes():
    import numpy as np

    from chordchart import beat_this

    samples = np.random.default_rng(0).uniform(-0.1, 0.1, 44_100 * 7).astype(np.float32)
    spect = beat_this.log_mel(samples[::2])  # ~22.05 kHz
    assert spect.shape == (1 + len(samples[::2]) // beat_this.HOP, 128)
    beat, down = beat_this.BeatThisModel().logits(samples)
    assert beat.shape == down.shape == (1 + 7 * 22050 // beat_this.HOP,)
    assert np.isfinite(beat).all() and beat.max() > -1000  # every frame was predicted
    activations = beat_this.dbn_activations(beat, down)
    assert activations.shape == (len(beat), 2) and (activations > 0).all()
