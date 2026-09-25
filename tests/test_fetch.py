import wave

import pytest

from chordchart.errors import AudioDecodeError, AudioRejectedError, FfmpegNotFoundError
from chordchart.fetch import SAMPLE_RATE, decode_to_wav, read_wav


def test_decodes_stereo_48k_mp3_to_standard_wav(make_audio, tmp_path):
    src = make_audio("tone", "sine=frequency=440:sample_rate=48000", 6)
    dst = tmp_path / "out.wav"

    duration = decode_to_wav(src, dst)

    assert duration == pytest.approx(6.0, abs=0.1)
    with wave.open(str(dst), "rb") as f:
        assert (f.getnchannels(), f.getframerate(), f.getsampwidth()) == (1, SAMPLE_RATE, 2)
    samples = read_wav(dst)
    assert samples.dtype.name == "float32"
    # lavfi's sine is generated at amplitude 1/8, and ffmpeg's stereo->mono downmix
    # is -3 dB, so expect a peak around 0.09. The point is "signal present, scaled
    # to [-1, 1]", not an exact level.
    assert 0.01 < abs(samples).max() <= 1.0


def test_rejects_too_short(make_audio, tmp_path):
    src = make_audio("short", "sine=frequency=440", 3)
    with pytest.raises(AudioRejectedError, match="at least 5 s"):
        decode_to_wav(src, tmp_path / "out.wav")


def test_rejects_silence(make_audio, tmp_path):
    src = make_audio("silence", "anullsrc=r=44100:cl=stereo", 6)
    with pytest.raises(AudioRejectedError, match="silent"):
        decode_to_wav(src, tmp_path / "out.wav")


def test_rejects_too_long(make_audio, tmp_path):
    src = make_audio("long", "sine=frequency=440", 9)
    with pytest.raises(AudioRejectedError, match="max-duration"):
        decode_to_wav(src, tmp_path / "out.wav", max_duration=6.0)


def test_missing_file(tmp_path):
    with pytest.raises(AudioDecodeError, match="not found"):
        decode_to_wav(tmp_path / "nope.mp3", tmp_path / "out.wav")


def test_not_audio(tmp_path):
    src = tmp_path / "fake.mp3"
    src.write_text("this is not audio")
    with pytest.raises(AudioDecodeError, match="could not decode"):
        decode_to_wav(src, tmp_path / "out.wav")


def test_ffmpeg_missing(monkeypatch, tmp_path):
    src = tmp_path / "a.mp3"
    src.write_bytes(b"x")
    monkeypatch.setattr("chordchart.fetch.shutil.which", lambda name: None)
    with pytest.raises(FfmpegNotFoundError, match="winget install Gyan.FFmpeg"):
        decode_to_wav(src, tmp_path / "out.wav")
