"""Notes pipeline with the chord analysis, Demucs and basic-pitch faked."""

import json

import pytest

from chordchart.notes import pipeline, stems, transcribe
from chordchart.notes.model import Note
from chordchart.notes.pipeline import transcribe_notes
from chordchart.notes.stems import Stems

LEVELS = {"bass": -30.0, "guitar": -22.0, "piano": -40.0, "other": -60.0}


@pytest.fixture
def fakes(monkeypatch, sample_song, tmp_path):
    calls = {"analyze": [], "separate": [], "transcribe": []}

    def analyze(source, **kwargs):
        calls["analyze"].append((source, kwargs))
        return sample_song

    def separate(path, offset, duration, folder, **kwargs):
        calls["separate"].append((offset, duration, kwargs["refresh"]))
        if kwargs["progress"]:
            kwargs["progress"](1.0)
        return Stems(tmp_path, dict(LEVELS))

    def fake_transcribe(path, instrument, offset=0.0):
        calls["transcribe"].append((path.name, instrument, offset))
        return [
            Note(0.52, 0.98, 52, 80),  # beat 2 of bar 1 -> steps 4-8
            Note(2.01, 2.03, 60, 80),  # 20 ms: dropped
            Note(4.0, 5.0, 45, 90),
        ]

    monkeypatch.setattr(stems, "separate", separate)
    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(pipeline, "resolve_source", lambda s, **k: type("R", (), {"path": s})())
    return analyze, calls


def test_the_loudest_stem_is_transcribed_on_the_charts_grid(fakes, tmp_path):
    analyze, calls = fakes
    progress = []
    result = transcribe_notes(
        tmp_path / "song.wav", start=0.0, analyze_fn=analyze, progress=progress.append
    )
    assert result.instrument == "guitar" and result.automatic
    assert calls["transcribe"] == [("guitar.flac", "guitar", 0.0)]
    assert calls["separate"] == [(0.0, 10.0, False)]
    assert progress == [1.0]
    assert [(n.pitch, n.step, n.steps) for n in result.notes] == [(52, 4, 4), (45, 32, 8)]
    assert result.bar_steps == [0, 16, 32, 48, 64]
    assert result.chords[:3] == [(0, "C"), (16, "C"), (24, "G")]
    assert (result.section_start, result.section_end) == (0.0, 10.0)
    assert (result.bpm, result.meter, result.steps_per_beat) == (120.4, 4, 4)
    json.loads(result.to_json())  # the page receives it as JSON


def test_the_instrument_can_be_chosen(fakes, tmp_path):
    analyze, calls = fakes
    result = transcribe_notes(tmp_path / "song.wav", instrument="bass", analyze_fn=analyze)
    assert (result.instrument, result.automatic) == ("bass", False)
    assert calls["transcribe"][0][1] == "bass"


def test_an_unknown_instrument_fails_before_any_work(fakes, tmp_path):
    analyze, calls = fakes
    with pytest.raises(ValueError):
        transcribe_notes(tmp_path / "song.wav", instrument="drums", analyze_fn=analyze)
    assert calls["analyze"] == []


def test_the_section_is_passed_to_the_chord_analysis(fakes, tmp_path):
    analyze, calls = fakes
    transcribe_notes(tmp_path / "song.wav", start=65.0, end=100.0, analyze_fn=analyze)
    assert calls["analyze"][0][1]["start"] == 65.0
    assert calls["analyze"][0][1]["end"] == 100.0


def test_a_near_silent_stem_gives_a_warning_not_notes(fakes, tmp_path):
    analyze, calls = fakes
    result = transcribe_notes(tmp_path / "song.wav", instrument="other", analyze_fn=analyze)
    assert result.notes == [] and calls["transcribe"] == []
    assert result.warnings == ["There is almost no other in this song (-60 dB), so no notes."]


def test_notes_command_explains_a_lite_install(monkeypatch, capsys):
    from chordchart.notes import cli as notes_cli

    monkeypatch.setattr(notes_cli, "notes_available", lambda: False)
    assert notes_cli.main(["song.mp3"]) == 2
    assert "ChordChart Notes" in capsys.readouterr().err


def test_self_test_skips_notes_checks_in_lite(monkeypatch, tmp_path):
    from chordchart.desktop import selftest

    for name in ("_ffmpeg", "_deno", "_ytdlp", "_opencv", "_analysis"):
        monkeypatch.setattr(selftest, name, lambda: "ok")
    monkeypatch.setattr(selftest, "notes_available", lambda: False)
    monkeypatch.setattr(selftest, "_demucs", lambda: pytest.fail("ran a notes check"))
    report = tmp_path / "report.json"
    assert selftest.run(str(report)) == 0
    checks = json.loads(report.read_text())["checks"]
    assert checks["notes: basic-pitch"]["skipped"] and checks["notes: torch and Demucs"]["ok"]
