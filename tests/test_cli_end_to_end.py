"""The real `chordchart` command, models included."""

import pytest

from chordchart import cli, sources
from chordchart.download import Downloaded

LINK = "https://www.youtube.com/watch?v=abc123"


@pytest.mark.slow
def test_link_and_file_give_the_same_chart(monkeypatch, capsys, early_strum_track):
    # The "download" returns the same local file, so any difference would come from
    # the link path through the CLI and pipeline.
    monkeypatch.setattr(
        sources,
        "download",
        lambda url, **kw: Downloaded(early_strum_track, "Early Strum (video)", 21, url, False),
    )

    assert cli.main([str(early_strum_track)]) == 0
    from_file = capsys.readouterr().out
    assert cli.main([LINK]) == 0
    from_link = capsys.readouterr().out

    assert from_file.splitlines()[0] == "early_strum"
    assert from_link.splitlines()[0] == "Early Strum (video)"
    assert from_link.splitlines()[1:] == from_file.splitlines()[1:]


@pytest.mark.network
def test_real_youtube_link(capsys, tmp_path, monkeypatch):
    # A short section of a royalty-free instrumental (Kevin MacLeod, "Monkeys Spinning
    # Monkeys"; the Windows Sandbox test uses it too). It needs music: "Me at the zoo",
    # used before, is 19 s of speech, where Beat This! rightly finds no steady beat.
    monkeypatch.setenv("CHORDCHART_CACHE_DIR", str(tmp_path))
    code = cli.main(
        ["https://www.youtube.com/watch?v=2eZVbrO6Z1M", "--start", "0:20", "--end", "0:50"]
    )
    captured = capsys.readouterr()
    assert code == 0, captured.err
    lines = captured.out.splitlines()
    assert lines[0] == "Monkeys Spinning Monkeys"
    assert lines[1].startswith("Key: ") and "Time: 4/4" in lines[1]
    assert sum(line.startswith("|") for line in lines) >= 3  # several rows of bars
    # Stage lines (with times) surround the download message; only its presence matters.
    assert "downloading: Monkeys Spinning Monkeys (" in captured.err
    assert "\ntotal: " in captured.err
