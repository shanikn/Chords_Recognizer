import wave

import pytest

from chordchart.errors import AudioDecodeError, AudioRejectedError, FfmpegNotFoundError
from chordchart.fetch import SAMPLE_RATE, decode_section, decode_to_wav, read_wav
from chordchart.timecode import format_time, parse_time


def test_decodes_stereo_48k_mp3_to_standard_wav(make_audio, tmp_path):
    src = make_audio("tone", "sine=frequency=440:sample_rate=48000", 6)
    dst = tmp_path / "out.wav"

    duration = decode_to_wav(src, dst)

    assert duration == pytest.approx(6.0, abs=0.1)
    with wave.open(str(dst), "rb") as f:
        assert (f.getnchannels(), f.getframerate(), f.getsampwidth()) == (1, SAMPLE_RATE, 2)
    samples = read_wav(dst)
    assert samples.dtype.name == "float32"
    # lavfi's sine is generated at amplitude 1/8. The fixture's -ac 2 upmix splits it
    # at 0.707 per channel (-3 dB), MP3 costs ~0.4 dB, and our stereo->mono downmix
    # (0.5*L + 0.5*R) is lossless, so expect a peak around 0.084. The point is
    # "signal present, scaled to [-1, 1]", not an exact level.
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
    monkeypatch.setattr("chordchart.bundled.ffmpeg_path", lambda: None)
    with pytest.raises(FfmpegNotFoundError, match="winget install Gyan.FFmpeg"):
        decode_to_wav(src, tmp_path / "out.wav")


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("75", 75.0),
        ("1:15", 75.0),
        ("1:02:03", 3723.0),
        ("1:15.5", 75.5),
        ("0", 0.0),
        ("90.25", 90.25),
    ],
)
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "1:75", "1:2:3:4", "-5", "1:-5", "1::5", "1:60:00"])
def test_parse_time_rejects(text):
    with pytest.raises(ValueError):
        parse_time(text)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (300, "5:00"),
        (75.5, "1:15.5"),
        (3723, "1:02:03"),
        (0, "0:00"),
        (59.94, "0:59.9"),
        (59.96, "1:00"),  # rounding carries into the minutes
        (3599.96, "1:00:00"),
    ],
)
def test_format_time(seconds, text):
    assert format_time(seconds) == text


@pytest.fixture(scope="module")
def tone_20s(make_audio):
    # WAV, so section lengths aren't blurred by MP3 encoder padding.
    return make_audio("tone20", "sine=frequency=440", 20, ext="wav")


def test_section_start_and_end(tone_20s, tmp_path):
    assert decode_to_wav(tone_20s, tmp_path / "o.wav", start=5, end=12) == pytest.approx(
        7.0, abs=0.05
    )


def test_section_start_only_reads_to_the_end(tone_20s, tmp_path):
    assert decode_to_wav(tone_20s, tmp_path / "o.wav", start=12) == pytest.approx(8.0, abs=0.05)


def test_section_end_only(tone_20s, tmp_path):
    assert decode_to_wav(tone_20s, tmp_path / "o.wav", end=6) == pytest.approx(6.0, abs=0.05)


def test_section_start_past_the_end(tone_20s, tmp_path):
    with pytest.raises(AudioRejectedError, match="--start 5:00 is past the end of the audio"):
        decode_to_wav(tone_20s, tmp_path / "o.wav", start=300)


def test_section_end_must_follow_start(tone_20s, tmp_path):
    with pytest.raises(AudioRejectedError, match="--end must be after --start"):
        decode_to_wav(tone_20s, tmp_path / "o.wav", start=8, end=8)


def test_short_section_names_the_section(tone_20s, tmp_path):
    with pytest.raises(AudioRejectedError, match="selected section is only 3.0 s long"):
        decode_to_wav(tone_20s, tmp_path / "o.wav", start=2, end=5)


def test_padded_section_reports_where_the_audio_starts(tone_20s, tmp_path):
    got = decode_section(tone_20s, tmp_path / "o.wav", start=8, end=14, pad=5)
    assert got.offset == 3.0
    assert got.duration == pytest.approx(16.0, abs=0.05)  # 3 s .. 19 s


def test_padding_is_clamped_to_the_file(tone_20s, tmp_path):
    got = decode_section(tone_20s, tmp_path / "o.wav", start=2, end=18, pad=5)
    assert got.offset == 0.0
    assert got.duration == pytest.approx(20.0, abs=0.05)


def test_padding_does_not_hide_a_start_past_the_end(tone_20s, tmp_path):
    # The padding (19-22 s) still contains a second of audio; the request doesn't.
    with pytest.raises(AudioRejectedError, match="--start 0:22 is past the end"):
        decode_section(tone_20s, tmp_path / "o.wav", start=22, pad=5)


def test_padding_does_not_rescue_a_short_section(tone_20s, tmp_path):
    with pytest.raises(AudioRejectedError, match="selected section is only 3.0 s long"):
        decode_section(tone_20s, tmp_path / "o.wav", start=8, end=11, pad=5)


def test_packaged_app_uses_its_own_ffmpeg(monkeypatch, tmp_path):
    from chordchart import bundled

    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setattr(bundled, "FROZEN", True)
    monkeypatch.setattr(bundled.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(bundled.shutil, "which", lambda name: pytest.fail("PATH must not be used"))
    assert bundled.ffmpeg_path() == str(tmp_path / "bin" / "ffmpeg.exe")
    assert bundled.deno_path() is None  # not bundled here, and never taken from the PC
