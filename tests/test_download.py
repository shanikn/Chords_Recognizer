"""download.py without the network: yt-dlp is replaced by FakeYDL."""

import json
from pathlib import Path

import pytest
from yt_dlp.networking.exceptions import TransportError
from yt_dlp.utils import DownloadError, ExtractorError, UnsupportedError

from chordchart.download import classify, default_cache_dir, download
from chordchart.errors import (
    AudioRejectedError,
    DownloadFailedError,
    InvalidLinkError,
    NetworkError,
    VideoUnavailableError,
)

URL = "https://www.youtube.com/watch?v=abc123"
SHORT_URL = "https://youtu.be/abc123"
VIDEO = {
    "extractor": "youtube",
    "id": "abc123",
    "title": "Test Song",
    "duration": 200,
    "ext": "webm",
    "webpage_url": URL,
}


def _wrap(exc: Exception) -> DownloadError:
    """Wrap an exception the way YoutubeDL.extract_info does."""
    return DownloadError(f"ERROR: {exc}", (type(exc), exc, None))


@pytest.fixture
def fake_ydl():
    class FakeYDL:
        calls: list[tuple[str, bool]] = []
        infos: dict[str, dict | Exception] = {URL: VIDEO, SHORT_URL: VIDEO}

        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def prepare_filename(self, info):
            return self.params["outtmpl"] % info

        def extract_info(self, url, download=True):
            FakeYDL.calls.append((url, download))
            result = FakeYDL.infos[url]
            if isinstance(result, Exception):
                raise result
            info = dict(result)
            if download:
                path = Path(self.prepare_filename(info))
                path.write_bytes(b"fake audio")
                info["requested_downloads"] = [{"filepath": str(path)}]
            return info

    return FakeYDL


def _download(fake, cache, url=URL, **kw):
    return download(url, cache_dir=cache, ydl_class=fake, **kw)


def test_first_call_downloads_and_records(fake_ydl, tmp_path):
    got = _download(fake_ydl, tmp_path)

    assert fake_ydl.calls == [(URL, False), (URL, True)]
    assert got.path == tmp_path / "youtube-abc123.webm"
    assert got.path.read_bytes() == b"fake audio"
    assert (got.title, got.duration, got.cached) == ("Test Song", 200, False)
    assert json.loads((tmp_path / "index.json").read_text()) == {URL: "youtube-abc123"}
    assert json.loads((tmp_path / "youtube-abc123.json").read_text())["title"] == "Test Song"


def test_same_link_again_makes_no_calls(fake_ydl, tmp_path):
    _download(fake_ydl, tmp_path)
    fake_ydl.calls.clear()

    got = _download(fake_ydl, tmp_path)

    assert fake_ydl.calls == []  # served from the index: no network at all
    assert (got.path, got.title, got.cached) == (
        tmp_path / "youtube-abc123.webm",
        "Test Song",
        True,
    )


def test_other_link_to_same_video_reuses_the_file(fake_ydl, tmp_path):
    _download(fake_ydl, tmp_path)
    fake_ydl.calls.clear()

    got = _download(fake_ydl, tmp_path, url=SHORT_URL)

    assert fake_ydl.calls == [(SHORT_URL, False)]  # metadata only, no download
    assert got.cached
    assert json.loads((tmp_path / "index.json").read_text())[SHORT_URL] == "youtube-abc123"


def test_refresh_downloads_again(fake_ydl, tmp_path):
    _download(fake_ydl, tmp_path)
    fake_ydl.calls.clear()

    got = _download(fake_ydl, tmp_path, refresh=True)

    assert fake_ydl.calls == [(URL, False), (URL, True)]
    assert not got.cached


def test_partial_download_is_not_a_hit(fake_ydl, tmp_path):
    (tmp_path / "index.json").write_text(json.dumps({URL: "youtube-abc123"}))
    (tmp_path / "youtube-abc123.json").write_text(json.dumps(VIDEO))
    (tmp_path / "youtube-abc123.webm.part").write_bytes(b"half")

    got = _download(fake_ydl, tmp_path)

    assert fake_ydl.calls == [(URL, False), (URL, True)]
    assert got.path == tmp_path / "youtube-abc123.webm"


@pytest.mark.parametrize(
    ("info", "error", "message"),
    [
        (
            {"_type": "playlist", "extractor": "youtube:tab", "id": "recommended"},
            InvalidLinkError,
            "playlist",
        ),
        ({**VIDEO, "live_status": "is_live"}, InvalidLinkError, "live stream"),
        ({**VIDEO, "duration": 5530}, AudioRejectedError, "raise --max-duration"),
    ],
)
def test_rejected_before_download(fake_ydl, tmp_path, info, error, message):
    fake_ydl.infos = {URL: info}
    with pytest.raises(error, match=message):
        _download(fake_ydl, tmp_path)
    assert (URL, True) not in fake_ydl.calls


def test_too_long_message_names_length_and_limit(fake_ydl, tmp_path):
    fake_ydl.infos = {URL: {**VIDEO, "duration": 5530}}
    with pytest.raises(AudioRejectedError) as exc:
        _download(fake_ydl, tmp_path, max_duration=900)
    assert str(exc.value) == (
        "video is 1:32:10 long, over the 15 min limit; raise --max-duration to download it"
    )


def test_yt_dlp_errors_are_classified_during_download(fake_ydl, tmp_path):
    fake_ydl.infos = {URL: _wrap(TransportError("[WinError 10061] refused"))}
    with pytest.raises(NetworkError):
        _download(fake_ydl, tmp_path)


def test_cache_dir_env_var(monkeypatch, tmp_path):
    monkeypatch.setenv("CHORDCHART_CACHE_DIR", str(tmp_path / "mine"))
    assert default_cache_dir() == tmp_path / "mine"


def test_default_cache_dir_is_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv("CHORDCHART_CACHE_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_cache_dir() == tmp_path / "chordchart" / "cache"


# classify(): real yt-dlp exception classes, wrapped exactly as the 2026-09-25 probe showed.

UNAVAILABLE = ExtractorError(
    "This video is unavailable", expected=True, video_id="aaaaaaaaaaa", ie="youtube"
)
PRIVATE = ExtractorError(
    "Private video. Sign in if you've been granted access to this video",
    expected=True,
    video_id="xyz",
    ie="youtube",
)
NESTED_NETWORK = ExtractorError(
    "Unable to download API page",
    cause=TransportError("getaddrinfo failed"),
    video_id="id1",
    ie="youtube",
)
UNEXPECTED = ExtractorError("Something changed on the page", video_id="id2", ie="youtube")


@pytest.mark.parametrize(
    ("exc", "error", "message"),
    [
        (UNAVAILABLE, VideoUnavailableError, "video unavailable: This video is unavailable"),
        (PRIVATE, VideoUnavailableError, "video unavailable: Private video. Sign in if"),
        (
            UnsupportedError("https://example.com/"),
            InvalidLinkError,
            "not a single-video link: https://example.com/ (unsupported site)",
        ),
        (
            TransportError("[WinError 10061] refused"),
            NetworkError,
            "network error: could not reach www.youtube.com; check your internet connection",
        ),
        (NESTED_NETWORK, NetworkError, "network error: could not reach www.youtube.com"),
        (
            UNEXPECTED,
            DownloadFailedError,
            "download failed: Something changed on the page. YouTube changes often; "
            "try: uv lock --upgrade-package yt-dlp && uv sync",
        ),
    ],
)
def test_classify(exc, error, message):
    # classify() names the link the user gave; UnsupportedError carries its own.
    err = classify(_wrap(exc), getattr(exc, "url", URL))
    assert isinstance(err, error)
    assert str(err).startswith(message)


@pytest.mark.parametrize("exc", [UNAVAILABLE, PRIVATE, NESTED_NETWORK, UNEXPECTED])
def test_classified_messages_are_one_clean_line(exc):
    text = str(classify(_wrap(exc), URL))
    assert "\n" not in text
    assert "ERROR:" not in text
    assert "[youtube]" not in text
    assert "please report" not in text
    assert "caused by" not in text
