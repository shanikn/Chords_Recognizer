"""chordchart/spotify.py: Spotify track links -> a YouTube video, with the Spotify API,
the track page and the YouTube search all mocked (no network)."""

import json
import urllib.error

import pytest

from chordchart import spotify
from chordchart.errors import InvalidLinkError, SpotifyMatchError
from chordchart.spotify import Match, Track, Video

TRACK_ID = "3n3Ppam7vgaVa1iaRUc9Lp"
LINK = f"https://open.spotify.com/track/{TRACK_ID}?si=abc123"
PAGE = """<html><head>
<meta property="og:title" content="Mr. Brightside"/>
<meta property="og:description" content="The Killers · Hot Fuss · Song · 2004"/>
<meta name="music:duration" content="222"/>
<meta name="music:musician_description" content="The Killers"/>
</head></html>"""


def page_http(request):
    url = request if isinstance(request, str) else request.full_url
    assert url == f"https://open.spotify.com/track/{TRACK_ID}"
    return PAGE.encode()


def results(*videos):
    return lambda query: list(videos)


TOPIC = Video("topic1234567", "Mr. Brightside", "The Killers - Topic", 223)
OFFICIAL = Video("official1234", "The Killers - Mr. Brightside (Official Music Video)", "TheKillersVEVO", 243)
LIVE = Video("live12345678", "Mr. Brightside (Live)", "Some Fan", 221)


# --- links --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        LINK,
        f"https://open.spotify.com/intl-de/track/{TRACK_ID}",
        f"https://open.spotify.com/embed/track/{TRACK_ID}",
        f"spotify:track:{TRACK_ID}",
    ],
)
def test_track_links(link):
    assert spotify.is_spotify(link)
    assert spotify.track_id(link) == TRACK_ID


@pytest.mark.parametrize(
    ("link", "what"),
    [
        ("https://open.spotify.com/album/4OHNH3sDzIxnmUADXzv2kT", "an album"),
        ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "a playlist"),
        ("https://open.spotify.com/artist/0C0XlULifJtAgn6ZNCW2eu", "an artist"),
        ("https://open.spotify.com/episode/512ojhOuo1ktJprKbVcKyQ", "a podcast episode"),
        ("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M", "a playlist"),
    ],
)
def test_other_spotify_links_are_refused_with_the_reason(link, what):
    with pytest.raises(InvalidLinkError, match=what) as err:
        spotify.track_id(link)
    assert "single-track" in str(err.value)


def test_short_links_explain_what_to_do():
    with pytest.raises(InvalidLinkError, match="open.spotify.com/track"):
        spotify.track_id("https://spotify.link/AbCdEf")


def test_a_malformed_track_link_is_refused():
    with pytest.raises(InvalidLinkError, match="not a Spotify track link"):
        spotify.track_id("https://open.spotify.com/track/short")


def test_youtube_is_not_spotify():
    assert not spotify.is_spotify("https://www.youtube.com/watch?v=NrgmdOz227I")
    assert not spotify.is_spotify(r"C:\music\song.mp3")


# --- metadata -----------------------------------------------------------------------


def test_metadata_from_the_page_without_a_key(monkeypatch, tmp_path):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    track = spotify.fetch_track(TRACK_ID, data_dir=tmp_path, http=page_http)
    assert track == Track(TRACK_ID, "Mr. Brightside", ["The Killers"], 222.0)


def test_metadata_from_the_api_with_a_key(monkeypatch, tmp_path):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "secret")
    seen = []

    def http(request):
        seen.append(request.full_url)
        if request.full_url.endswith("/api/token"):
            assert request.headers["Authorization"].startswith("Basic ")
            return json.dumps({"access_token": "T"}).encode()
        assert request.headers["Authorization"] == "Bearer T"
        return json.dumps(
            {"name": "Mr. Brightside", "duration_ms": 222075, "artists": [{"name": "The Killers"}]}
        ).encode()

    track = spotify.fetch_track(TRACK_ID, data_dir=tmp_path, http=http)
    assert track == Track(TRACK_ID, "Mr. Brightside", ["The Killers"], 222.075)
    assert seen[-1] == f"https://api.spotify.com/v1/tracks/{TRACK_ID}"


def test_key_from_settings_file(monkeypatch, tmp_path):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    (tmp_path / "settings.json").write_text(
        json.dumps({"spotify_client_id": "a", "spotify_client_secret": "b"})
    )
    assert spotify.credentials(tmp_path) == ("a", "b")


def test_a_rejected_key_falls_back_to_the_page(monkeypatch, tmp_path):
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "id")
    monkeypatch.setenv("SPOTIFY_CLIENT_SECRET", "wrong")

    def http(request):
        if request.full_url.endswith("/api/token"):
            raise urllib.error.HTTPError(request.full_url, 400, "bad client", {}, None)
        return page_http(request)

    assert spotify.fetch_track(TRACK_ID, data_dir=tmp_path, http=http).title == "Mr. Brightside"


def test_unknown_track(monkeypatch, tmp_path):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)

    def http(request):
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)

    with pytest.raises(SpotifyMatchError, match="doesn't know this track"):
        spotify.fetch_track(TRACK_ID, data_dir=tmp_path, http=http)


# --- choosing a video ---------------------------------------------------------------

TRACK = Track(TRACK_ID, "Mr. Brightside", ["The Killers"], 222.0)


def test_prefers_the_artists_topic_channel():
    assert spotify.choose(TRACK, [LIVE, OFFICIAL, TOPIC]) == TOPIC


def test_otherwise_the_closest_length_within_3_seconds():
    far = Video("far123456789", "Mr. Brightside", "x", 224.5)
    near = Video("near12345678", "Mr. Brightside", "y", 221.5)
    assert spotify.choose(TRACK, [far, OFFICIAL, near]) == near


def test_a_topic_channel_of_another_artist_is_no_better():
    cover = Video("cover1234567", "Mr. Brightside", "Cover Band - Topic", 222.0)
    near = Video("near12345678", "Mr. Brightside", "y", 223.0)
    assert spotify.choose(TRACK, [cover, near]) == cover  # same rank: the closer length wins


def test_nothing_within_3_seconds_is_no_match():
    assert spotify.choose(TRACK, [OFFICIAL, Video("x2345678901", "t", "c", 226)]) is None
    assert spotify.choose(Track(TRACK_ID, "t", [], None), [TOPIC]) is None


# --- resolution and cache -----------------------------------------------------------


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SPOTIFY_CLIENT_SECRET", raising=False)
    downloads = tmp_path / "cache" / "downloads"
    downloads.mkdir(parents=True)
    return downloads


def test_match_searches_with_artist_and_title(folder):
    queries = []

    def search(query):
        queries.append(query)
        return [OFFICIAL, TOPIC]

    match = spotify.match_track(LINK, folder, http=page_http, search=search)
    assert queries == ["The Killers - Mr. Brightside"]
    assert match == Match(TRACK, TOPIC, "search")
    assert match.to_dict()["video"]["url"] == "https://www.youtube.com/watch?v=topic1234567"


def test_the_match_is_cached(folder):
    spotify.match_track(LINK, folder, http=page_http, search=results(TOPIC))

    def offline(*args):
        raise AssertionError("should not touch the network")

    again = spotify.match_track(f"spotify:track:{TRACK_ID}", folder, http=offline, search=offline)
    assert again.video == TOPIC


def test_an_old_search_match_is_redone(folder):
    spotify.remember(Match(TRACK, OFFICIAL, "search"), folder, now=1.0)
    match = spotify.match_track(LINK, folder, http=page_http, search=results(TOPIC))
    assert match.video == TOPIC


def test_no_confident_match(folder):
    with pytest.raises(SpotifyMatchError, match="no confident match.*3:42.*YouTube link"):
        spotify.match_track(LINK, folder, http=page_http, search=results(OFFICIAL))
    assert spotify.cached_match(TRACK_ID, folder) is None


def test_the_users_video_replaces_the_match_and_is_remembered(folder):
    spotify.match_track(LINK, folder, http=page_http, search=results(TOPIC))
    mine = Video("mine12345678", "Mr. Brightside (acoustic)", "Me", 230)
    match = spotify.match_track(
        LINK, folder, video_link="https://youtu.be/mine12345678", http=page_http,
        video_info=lambda url: mine,
    )  # fmt: skip
    assert match == Match(TRACK, mine, "user")
    assert spotify.cached_match(TRACK_ID, folder).video == mine


def test_resolve_source_downloads_the_matched_video(folder, monkeypatch):
    from chordchart import sources
    from chordchart.download import Downloaded

    monkeypatch.setenv("CHORDCHART_CACHE_DIR", str(folder.parent))
    spotify.remember(Match(TRACK, TOPIC, "search"), folder)
    fetched = []

    def download(url, **kwargs):
        fetched.append(url)
        return Downloaded(folder / "youtube-topic1234567.webm", "Mr. Brightside", 223, url, True)

    monkeypatch.setattr(sources, "download", download)
    resolved = sources.resolve_source(LINK)
    assert fetched == ["https://www.youtube.com/watch?v=topic1234567"]
    assert resolved.title == "The Killers - Mr. Brightside"
    assert resolved.source == "https://www.youtube.com/watch?v=topic1234567"
    assert resolved.match["spotify_url"] == f"https://open.spotify.com/track/{TRACK_ID}"


def test_youtube_links_are_unchanged(monkeypatch, tmp_path):
    from chordchart import sources
    from chordchart.download import Downloaded

    def download(url, **kwargs):
        return Downloaded(tmp_path / "a.webm", "A", 1, url, True)

    monkeypatch.setattr(sources, "download", download)
    resolved = sources.resolve_source("https://youtu.be/NrgmdOz227I")
    assert resolved.source == "https://www.youtube.com/watch?v=NrgmdOz227I"
    assert resolved.match is None


def test_refresh_redoes_a_search_but_keeps_the_users_choice(folder):
    spotify.remember(Match(TRACK, OFFICIAL, "search"), folder)
    assert spotify.match_track(LINK, folder, refresh=True, http=page_http, search=results(TOPIC)).video == TOPIC

    mine = Video("mine12345678", "Mr. Brightside (acoustic)", "Me", 230)
    spotify.remember(Match(TRACK, mine, "user"), folder)

    def no_search(query):
        raise AssertionError("the user's choice must not be searched again")

    assert spotify.match_track(LINK, folder, refresh=True, http=page_http, search=no_search).video == mine


def test_an_old_user_choice_keeps_its_video_but_refreshes_the_track_details(folder):
    mine = Video("mine12345678", "Mr. Brightside (acoustic)", "Me", 230)
    stale_track = Track(TRACK_ID, "Old Title", ["Old Artist"], 1.0)
    spotify.remember(Match(stale_track, mine, "user"), folder, now=1.0)

    def no_search(query):
        raise AssertionError("the user's choice must not be searched again")

    match = spotify.match_track(LINK, folder, http=page_http, search=no_search)
    assert match == Match(TRACK, mine, "user")  # details from Spotify again, same video
    assert spotify.cached_match(TRACK_ID, folder) == match
