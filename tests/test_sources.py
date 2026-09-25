from pathlib import Path

import pytest

from chordchart import sources
from chordchart.download import Downloaded
from chordchart.errors import AudioDecodeError
from chordchart.sources import is_link, normalize_link, resolve_source


@pytest.mark.parametrize(
    ("arg", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc", True),
        ("http://youtu.be/abc", True),
        ("song.mp3", False),
        (r"C:\music\song.mp3", False),  # a drive letter is not a URL scheme
        ("youtube.com/watch?v=abc", False),  # no scheme: treated as a path
        ("ftp://host/file.mp3", False),
        ("https://", False),
    ],
)
def test_is_link(arg, expected):
    assert is_link(arg) is expected


def test_link_goes_through_download(monkeypatch, tmp_path):
    seen = {}

    def fake_download(url, **kw):
        seen.update(kw, url=url)
        return Downloaded(tmp_path / "youtube-abc.webm", "My Song", 200, url, cached=False)

    monkeypatch.setattr(sources, "download", fake_download)
    status = []
    got = resolve_source(
        "https://youtu.be/abc", max_duration=60, refresh=True, status=status.append
    )

    assert got == sources.ResolvedSource(
        tmp_path / "youtube-abc.webm", "My Song", "https://youtu.be/abc"
    )
    assert (seen["url"], seen["max_duration"], seen["refresh"]) == (
        "https://youtu.be/abc",
        60,
        True,
    )
    assert seen["status"] == status.append


def test_path_is_used_as_is(tmp_path):
    song = tmp_path / "My Song.mp3"
    song.write_bytes(b"x")
    assert resolve_source(str(song)) == sources.ResolvedSource(song, "My Song", str(song))


def test_missing_path_that_looks_like_a_link_gets_a_hint():
    with pytest.raises(AudioDecodeError, match=r"add https://.*https://youtu\.be/abc"):
        resolve_source("youtu.be/abc")


def test_missing_ordinary_path_is_plain_not_found():
    with pytest.raises(AudioDecodeError) as exc:
        resolve_source("nope/song.mp3")
    assert str(exc.value) == f"file not found: {Path('nope/song.mp3')}"


VIDEO = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "link",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123&index=3&pp=iAQB",
        "https://www.youtube.com/watch?list=PL123&v=dQw4w9WgXcQ&t=42s",
        "https://youtu.be/dQw4w9WgXcQ?si=abcDEF&t=10",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDAMVM",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share",
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        VIDEO,
    ],
)
def test_normalize_link_keeps_only_the_video(link):
    assert normalize_link(link) == VIDEO


@pytest.mark.parametrize(
    "link",
    [
        "https://www.youtube.com/playlist?list=PL123",  # no video: download rejects it
        "https://www.youtube.com/watch?v=",  # malformed: download rejects it
        "https://vimeo.com/123456?share=copy",  # other sites untouched
        "https://www.youtube.com/watch?v=tooshort",
    ],
)
def test_normalize_link_leaves_other_links_alone(link):
    assert normalize_link(link) == link


def test_playlist_parameters_are_dropped_and_reported(monkeypatch, tmp_path):
    seen = {}

    def fake_download(url, **kw):
        seen["url"] = url
        return Downloaded(tmp_path / "v.webm", "Song", 200, url, cached=False)

    monkeypatch.setattr(sources, "download", fake_download)
    status = []
    got = resolve_source(VIDEO + "&list=RD1&start_radio=1", status=status.append)

    assert seen["url"] == VIDEO
    assert got.source == VIDEO
    assert status == [f"using the video only: {VIDEO}"]
