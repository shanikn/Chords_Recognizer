import pytest

from chordchart import analysis_cache, cli
from chordchart.analysis_cache import ModelOutputs, cache_key, load, store
from chordchart.beats import Beats
from chordchart.model import Key, Segment
from chordchart.pipeline import analyze

OUTPUTS = ModelOutputs(
    beats=Beats([0.5, 1.0, 1.5], [1, 2, 3], 120.0, 3),
    segments=[Segment(0.0, 1.2, "C:maj"), Segment(1.2, 2.0, "A:min")],
    key=Key("C", "major", 0.87),
)


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "a.wav"
    path.write_bytes(b"RIFF fake audio bytes")
    return path


def test_round_trip(tmp_path, wav):
    key = cache_key(wav, "madmom-crf", (3, 4))
    store(tmp_path, key, OUTPUTS)
    assert load(tmp_path, key) == OUTPUTS


def test_miss_and_corrupt_entries_are_none(tmp_path, wav):
    key = cache_key(wav, "madmom-crf", (3, 4))
    assert load(tmp_path, key) is None
    (tmp_path / f"{key}.json").write_text("{ not json")
    assert load(tmp_path, key) is None
    (tmp_path / f"{key}.json").write_text('{"beats": {}}')
    assert load(tmp_path, key) is None


def test_key_depends_on_audio_and_settings(tmp_path, wav, monkeypatch):
    base = cache_key(wav, "madmom-crf", (3, 4))
    other = tmp_path / "b.wav"
    other.write_bytes(b"RIFF other audio bytes")
    assert cache_key(other, "madmom-crf", (3, 4)) != base
    assert cache_key(wav, "chordino", (3, 4)) != base
    assert cache_key(wav, "madmom-crf", (2, 3, 4)) != base
    monkeypatch.setattr(analysis_cache, "CACHE_VERSION", analysis_cache.CACHE_VERSION + 1)
    assert cache_key(wav, "madmom-crf", (3, 4)) != base
    assert len(base) == 64


def test_clear_only_touches_our_entries(tmp_path, wav):
    store(tmp_path, cache_key(wav, "madmom-crf", (3, 4)), OUTPUTS)
    (tmp_path / "notes.txt").write_text("keep me")
    count, size = analysis_cache.summary(tmp_path)
    assert count == 1 and size > 0
    assert analysis_cache.clear(tmp_path) == (count, size)
    # Only the cached entry goes; the audio file and the note aren't ours.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.wav", "notes.txt"]


def test_cache_commands_include_analyses(isolated_cache, wav, capsys):
    folder = isolated_cache / "analysis"
    store(folder, cache_key(wav, "madmom-crf", (3, 4)), OUTPUTS)

    assert cli.main(["cache", "info"]) == 0
    out = capsys.readouterr().out
    assert f"analysis cache: {folder}" in out and "1 analysis" in out

    assert cli.main(["cache", "clear"]) == 0
    assert "removed 1 analysis" in capsys.readouterr().out
    assert analysis_cache.summary(folder) == (0, 0)


@pytest.mark.slow
def test_second_run_uses_the_cache(click_track):
    events = []
    first = analyze(click_track, status=lambda m, **kw: events.append(m))
    assert "using cached analysis" not in events
    events.clear()

    second = analyze(click_track, status=lambda m, **kw: events.append(m))

    assert "using cached analysis" in events
    assert not {"beats", "chords", "key"} & set(second.timings)  # models skipped
    assert (second.bars, second.key, second.debug) == (first.bars, first.key, first.debug)

    events.clear()
    analyze(click_track, refresh=True, status=lambda m, **kw: events.append(m))
    assert "using cached analysis" not in events  # --refresh recomputes

    events.clear()
    # 8-14 s decodes 3-19 s with its padding: different audio, so a different entry.
    # (4-16 s would decode the whole 20 s file: same audio, rightly the same entry.)
    analyze(click_track, start=8, end=14, status=lambda m, **kw: events.append(m))
    assert "using cached analysis" not in events
