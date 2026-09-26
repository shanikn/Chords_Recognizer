"""chordchart cache info | clear"""

import json

import pytest

from chordchart import cli


@pytest.fixture
def cache_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CHORDCHART_CACHE_DIR", str(tmp_path))
    return tmp_path


def _populate(root):
    folder = root / "downloads"
    folder.mkdir()
    (folder / "youtube-abc123.webm").write_bytes(b"x" * 1_500_000)
    (folder / "youtube-abc123.json").write_text(json.dumps({"title": "A"}))
    (folder / "youtube-def_45-6.m4a").write_bytes(b"x" * 500_000)
    (folder / "youtube-def_45-6.json").write_text(json.dumps({"title": "B"}))
    (folder / "youtube-ghi.webm.part").write_bytes(b"x" * 10)
    (folder / "index.json").write_text(json.dumps({"https://youtu.be/abc123": "youtube-abc123"}))
    return folder


def test_info_on_empty_cache(cache_root, capsys):
    assert cli.main(["cache", "info"]) == 0
    out = capsys.readouterr().out
    assert str(cache_root / "downloads") in out
    assert "0 downloads" in out


def test_info_counts_downloads_and_size(cache_root, capsys):
    _populate(cache_root)
    assert cli.main(["cache", "info"]) == 0
    out = capsys.readouterr().out
    assert "2 downloads" in out
    assert "2.0 MB" in out


def test_clear_removes_our_files(cache_root, capsys):
    folder = _populate(cache_root)
    assert cli.main(["cache", "clear"]) == 0
    assert list(folder.iterdir()) == []
    assert capsys.readouterr().out.startswith("removed 2 downloads (2.0 MB) from ")


def test_clear_refuses_a_folder_that_is_not_ours(cache_root, capsys):
    folder = cache_root / "downloads"
    folder.mkdir()
    (folder / "thesis.docx").write_bytes(b"important")
    assert cli.main(["cache", "clear"]) == 2
    assert (folder / "thesis.docx").exists()
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "not a chordchart cache" in err
    assert err.count("\n") == 1


def test_clear_leaves_unknown_files_in_our_folder(cache_root):
    folder = _populate(cache_root)
    (folder / "notes.txt").write_text("keep me")
    assert cli.main(["cache", "clear"]) == 0
    assert [p.name for p in folder.iterdir()] == ["notes.txt"]


def test_clear_on_missing_cache(cache_root, capsys):
    assert cli.main(["cache", "clear"]) == 0
    assert "cache is empty" in capsys.readouterr().out


def test_cache_without_action_is_a_usage_error(cache_root):
    with pytest.raises(SystemExit) as exc:
        cli.main(["cache"])
    assert exc.value.code == 2


def test_help_shows_cache_location(cache_root, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert str(cache_root / "downloads") in out
    assert "CHORDCHART_CACHE_DIR" in out
    assert "chordchart cache clear" in out
