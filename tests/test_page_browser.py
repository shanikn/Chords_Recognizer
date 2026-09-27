"""The page in a real browser (headless Chrome), lite and full, all offline.

    uv run --with playwright pytest -m browser

Skipped unless Playwright is importable and Chrome can be started. The server runs the
real app with fake analysis and transcription, so nothing touches the network.
"""

import socket
import threading
import time

import pytest

from chordchart.notes.model import Note, Transcription
from chordchart.web.server import create_app

playwright = pytest.importorskip("playwright.sync_api")
pytestmark = pytest.mark.browser


def _transcription(*args, **kwargs):
    notes = [Note(0, 0, 64, 90, 0, 4), Note(0, 0, 48, 80, 0, 16), Note(0, 0, 67, 90, 12, 8)]
    return Transcription(
        title="Browser test", source="x", instrument="piano", automatic=True,
        levels={"bass": -70.0, "guitar": -70.0, "piano": -20.0, "other": -70.0},
        notes=notes, bpm=100.0, meter=4, section_start=0.0, section_end=4.8,
        bar_steps=[0, 16], chords=[(0, "C"), (16, "G")], key_tonic="C", key_mode="major",
        steps=32,
    )  # fmt: skip


def _serve(notes: bool) -> str:
    import uvicorn

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(lambda *a, **k: None, notes_fn=_transcription, notes=notes)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            return f"http://127.0.0.1:{port}/"
        time.sleep(0.05)
    raise RuntimeError("test server didn't start")


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        try:
            chrome = p.chromium.launch(channel="chrome")
        except Exception as exc:  # no Chrome here
            pytest.skip(f"Chrome not available: {exc}")
        yield chrome
        chrome.close()


def _open(browser, url):
    page = browser.new_page()
    seen = {"requests": [], "errors": []}
    page.on("request", lambda r: seen["requests"].append(r.url))
    page.on("console", lambda m: m.type == "error" and seen["errors"].append(m.text))
    page.on("pageerror", lambda e: seen["errors"].append(str(e)))
    page.goto(url)
    page.wait_for_load_state("networkidle")
    return page, seen


def test_lite_never_requests_the_sheet_renderer(browser):
    url = _serve(notes=False)
    page, seen = _open(browser, url)
    assert page.is_hidden("#views") and page.is_hidden("#notes-credit")
    # Even asked directly (as the Sheet view would), it refuses without a request.
    message = page.evaluate("loadSheetRenderer().then(() => 'loaded', (e) => e.message)")
    assert "not included" in message
    page.wait_for_timeout(300)
    assert not [u for u in seen["requests"] if "/vendor/" in u]
    assert seen["errors"] == []
    assert all(u.startswith(url) for u in seen["requests"])
    page.close()


def test_full_draws_the_sheet_from_the_local_renderer(browser):
    url = _serve(notes=True)
    page, seen = _open(browser, url)
    assert not [u for u in seen["requests"] if "/vendor/" in u]  # not until Sheet is opened
    page.fill("#source", "song.mp3")  # the fake transcriber ignores it
    page.click("#view-notes")
    page.click("#transcribe")
    page.wait_for_selector("#notes-views:not([hidden])", timeout=10_000)
    assert page.is_visible("#roll")
    page.click("#show-sheet")
    page.wait_for_selector("#sheet svg", timeout=30_000)
    vendor = [u for u in seen["requests"] if "/vendor/" in u]
    assert vendor == [f"{url}vendor/opensheetmusicdisplay.min.js"]
    assert all(u.startswith(url) for u in seen["requests"])  # nothing from the web
    assert seen["errors"] == []
    page.close()
