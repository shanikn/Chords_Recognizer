"""chordchart serve: the local web UI (analysis replaced by a fake)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

from chordchart import cli
from chordchart.errors import VideoUnavailableError
from chordchart.render.text import render_text
from chordchart.web import server
from chordchart.web.server import create_app

LINK = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RD1"
JSON = {"Content-Type": "application/json"}


def _client(analyze_fn):
    # TestClient's default host is "testserver"; the app only accepts localhost.
    return TestClient(create_app(analyze_fn), base_url="http://127.0.0.1:8765")


def _wait(client, job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["state"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError(f"job still running: {job}")


def test_page_is_served():
    response = _client(lambda *a, **k: None).get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Analyze" in response.text


def test_analysis_reports_progress_then_the_chart(sample_song):
    seen = {}

    def fake_analyze(source, **kw):
        seen.update(kw, source=source)
        kw["status"]("getting audio")
        kw["status"]("downloading: My Song (3:45)")
        kw["status"]("getting audio", elapsed=3.14159)
        kw["status"]("tracking beats")
        kw["status"]("tracking beats", elapsed=4.2)
        return sample_song

    client = _client(fake_analyze)
    response = client.post(
        "/api/analyze", json={"source": f'  "{LINK}" ', "start": "1:05", "end": "2:10"}
    )
    assert response.status_code == 200
    job = _wait(client, response.json()["job_id"])

    assert job["state"] == "done"
    # A finished stage gets its time on the entry it started, even when other
    # messages came in between.
    assert job["messages"] == [
        {"text": "getting audio", "seconds": 3.14},
        {"text": "downloading: My Song (3:45)", "seconds": None},
        {"text": "tracking beats", "seconds": 4.2},
    ]
    assert job["chart"] == render_text(sample_song)
    assert job["song"]["title"] == "Test Song"
    assert (seen["source"], seen["start"], seen["end"]) == (LINK, 65.0, 130.0)


def test_blank_times_mean_whole_song(sample_song):
    seen = {}

    def fake_analyze(source, **kw):
        seen.update(kw)
        return sample_song

    client = _client(fake_analyze)
    job_id = client.post("/api/analyze", json={"source": LINK, "start": "", "end": " "}).json()
    _wait(client, job_id["job_id"])
    assert (seen["start"], seen["end"]) == (0.0, None)


def test_analysis_errors_are_shown_as_one_line():
    def boom(source, **kw):
        raise VideoUnavailableError("video unavailable: Private video")

    client = _client(boom)
    job = _wait(client, client.post("/api/analyze", json={"source": LINK}).json()["job_id"])
    assert (job["state"], job["error"]) == ("error", "video unavailable: Private video")


def test_unexpected_errors_are_reported_not_hidden():
    def crash(source, **kw):
        raise RuntimeError("model exploded")

    client = _client(crash)
    job = _wait(client, client.post("/api/analyze", json={"source": LINK}).json()["job_id"])
    assert job["state"] == "error"
    assert job["error"] == "internal error: RuntimeError: model exploded (details in the terminal)"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"source": "  "}, "enter a link or a file path"),
        ({"source": LINK, "start": "1:75"}, "minutes and seconds must be below 60"),
        ({"source": LINK, "start": "2:00", "end": "1:00"}, "the end must be after the start"),
    ],
)
def test_bad_input_is_rejected_before_starting(body, message):
    client = _client(lambda *a, **k: pytest.fail("analysis should not start"))
    response = client.post("/api/analyze", json=body)
    assert response.status_code == 400
    assert message in response.json()["error"]


def test_only_json_posts_are_accepted():
    # A cross-site form can only send form/text bodies without a CORS preflight.
    client = _client(lambda *a, **k: pytest.fail("analysis should not start"))
    response = client.post(
        "/api/analyze", content='{"source": "x"}', headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415


def test_other_host_names_are_refused():
    # Blocks DNS rebinding: a hostile domain resolving to 127.0.0.1.
    client = TestClient(create_app(lambda *a, **k: None), base_url="http://evil.example:8765")
    assert client.get("/").status_code == 400


def test_unknown_job():
    assert _client(lambda *a, **k: None).get("/api/jobs/nope").status_code == 404


def test_jobs_run_one_at_a_time(sample_song):
    running = []
    overlap = []
    release = threading.Event()

    def slow(source, **kw):
        running.append(source)
        if len(running) > 1:
            overlap.append(True)
        release.wait(2)
        running.remove(source)
        return sample_song

    client = _client(slow)
    first = client.post("/api/analyze", json={"source": "a.mp3"}).json()["job_id"]
    second = client.post("/api/analyze", json={"source": "b.mp3"}).json()["job_id"]
    time.sleep(0.1)
    queued = client.get(f"/api/jobs/{second}").json()
    assert queued["messages"] == [
        {"text": "waiting for the previous analysis to finish", "seconds": None}
    ]
    release.set()
    assert _wait(client, first)["state"] == "done"
    assert _wait(client, second)["state"] == "done"
    assert overlap == []


# `chordchart serve`


def test_serve_binds_localhost_and_opens_the_browser(monkeypatch):
    calls = {}
    monkeypatch.setattr(server, "_port_is_free", lambda port: True)
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kw: calls.update(kw))
    monkeypatch.setattr(server, "_open_browser_soon", lambda url: calls.update(url=url))

    assert cli.main(["serve", "--port", "9000"]) == 0
    assert (calls["host"], calls["port"]) == ("127.0.0.1", 9000)
    assert calls["url"] == "http://127.0.0.1:9000/"


def test_serve_no_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(server, "_port_is_free", lambda port: True)
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setattr(server, "_open_browser_soon", opened.append)
    assert cli.main(["serve", "--no-browser"]) == 0
    assert opened == []


def test_serve_port_in_use_is_one_line(monkeypatch, capsys):
    monkeypatch.setattr(server, "_port_is_free", lambda port: False)
    assert cli.main(["serve", "--port", "9000"]) == 2
    assert capsys.readouterr().err == (
        "error: port 9000 is already in use; try: chordchart serve --port 9001\n"
    )


def test_server_loads_processors_once_and_closes_them_on_shutdown(monkeypatch, sample_song):
    import chordchart.pipeline
    import chordchart.processors

    built, closed, used = [], [], []

    class FakeProcessors:
        def __init__(self, **kwargs):
            built.append(kwargs)

        def close(self):
            closed.append(True)

    def fake_analyze(source, **kw):
        used.append(kw["processors"])
        return sample_song

    monkeypatch.setattr(chordchart.processors, "Processors", FakeProcessors)
    monkeypatch.setattr(chordchart.pipeline, "analyze", fake_analyze)

    app = create_app()  # the real wiring, with the heavy parts replaced
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for source in ("a.mp3", "b.mp3"):
            _wait(client, client.post("/api/analyze", json={"source": source}).json()["job_id"])
        assert closed == []
    assert len(built) == 1  # one set for the server's lifetime
    assert len(used) == 2 and used[0] is used[1]  # reused by every analysis
    assert closed == [True]  # closed on shutdown
