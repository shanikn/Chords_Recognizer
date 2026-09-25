"""Real YouTube downloads. Excluded by default; run with: uv run pytest -m network"""

import pytest
import yt_dlp

from chordchart.download import download
from chordchart.errors import VideoUnavailableError

pytestmark = pytest.mark.network

# "Me at the zoo": the first YouTube video (2005), 19 s, public and very stable.
ZOO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def test_downloads_then_serves_from_cache(tmp_path, monkeypatch):
    first = download(ZOO, cache_dir=tmp_path)
    assert first.path.is_file() and first.path.stat().st_size > 10_000
    assert (first.title, first.duration, first.cached) == ("Me at the zoo", 19, False)

    def no_network(*args, **kwargs):
        raise AssertionError("cache miss: yt-dlp was called")

    monkeypatch.setattr(yt_dlp.YoutubeDL, "extract_info", no_network)
    second = download(ZOO, cache_dir=tmp_path)
    assert (second.path, second.cached) == (first.path, True)


def test_nonexistent_video(tmp_path):
    with pytest.raises(VideoUnavailableError, match="unavailable"):
        download("https://www.youtube.com/watch?v=aaaaaaaaaaa", cache_dir=tmp_path)
