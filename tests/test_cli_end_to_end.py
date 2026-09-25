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
    monkeypatch.setenv("CHORDCHART_CACHE_DIR", str(tmp_path))
    code = cli.main(["https://www.youtube.com/watch?v=jNQXAC9IVRw"])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert captured.out.splitlines()[0] == "Me at the zoo"
    assert captured.err.startswith("downloading: Me at the zoo (0:19)")
