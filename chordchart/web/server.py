"""`chordchart serve`: a small local web UI over the same pipeline as the CLI.

    GET  /                 the page (index.html next to this file)
    POST /api/analyze      {"source", "start", "end"} -> {"job_id"}
    GET  /api/jobs/{id}    {"state": running|done|error, "messages", "chart", "song", "error"}
    POST /api/notes        {"source", "start", "end", "instrument"} -> {"job_id"}; the job's
                           "notes" is the Transcription, "progress" the stem separation's
    GET  /api/notes/{id}.mid   that job's notes as a MIDI file

The page polls the job while it runs. Analyses run one at a time on a worker thread:
the models already use the whole CPU, so running two at once would only slow both.

Local only, on purpose:
- it binds to 127.0.0.1, so nothing else on the network can reach it;
- it accepts only Host: 127.0.0.1/localhost, which blocks DNS rebinding (a hostile
  domain that resolves to 127.0.0.1 to get past the browser's same-origin rules);
- POST needs Content-Type: application/json. Other websites can't send that
  cross-origin without a CORS preflight, which this server never approves, so they
  can't start analyses through your browser.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from functools import partial
from pathlib import Path
from urllib.parse import quote

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from chordchart.errors import ChordChartError
from chordchart.interactive import unquote
from chordchart.notes.model import INSTRUMENTS
from chordchart.render.text import render_text
from chordchart.timecode import parse_time

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PAGE = Path(__file__).with_name("index.html")
QUEUED = "waiting for the previous analysis to finish"


@dataclass
class Job:
    state: str = "running"  # running | done | error
    # Progress, oldest first: {"text", "seconds" (when finished), "running"}. Several
    # stages can be running at once; plain messages are never "running".
    messages: list[dict] = field(default_factory=list)
    chart: str | None = None  # render_text(song)
    song: dict | None = None  # Song JSON
    notes: dict | None = None  # Transcription JSON (notes jobs)
    progress: float | None = None  # notes jobs: stem separation, 0..1
    error: str | None = None


@dataclass
class Desktop:
    """Extra behaviour when the server runs inside the Windows desktop app.

    The page shows a "Quit ChordChart" button (POST /api/quit calls `on_quit`) and
    pings /api/ping while it's open, so the app can exit on its own once no page has
    been open for a while (see chordchart.desktop.app).
    """

    on_quit: Callable[[], None]
    version: str
    last_seen: float = field(default_factory=time.monotonic)


def create_app(
    analyze_fn: Callable | None = None,
    desktop: Desktop | None = None,
    notes_fn: Callable | None = None,
) -> FastAPI:
    """The app. `analyze_fn` defaults to pipeline.analyze with one set of processors
    kept loaded for the server's lifetime; tests pass a fake instead. `notes_fn`
    defaults to notes.pipeline.transcribe_notes using that same `analyze_fn`. `desktop`
    adds the desktop app's quit button and heartbeat."""
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chordchart-job")
    loaded: Future | None = None
    if analyze_fn is None:
        from chordchart.pipeline import DEFAULT_THREADS, analyze
        from chordchart.processors import Processors

        # Built on the worker, so the page is up at once and the first analysis
        # simply queues behind the model loading.
        loaded = worker.submit(Processors, num_threads=DEFAULT_THREADS)

        def analyze_fn(source: str, **kwargs):
            return analyze(source, processors=loaded.result(), **kwargs)

    if notes_fn is None:

        def notes_fn(source: str, **kwargs):
            from chordchart.notes.pipeline import transcribe_notes  # torch: only when used

            return transcribe_notes(source, analyze_fn=analyze_fn, **kwargs)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        worker.shutdown(wait=False, cancel_futures=True)
        if loaded is not None and loaded.done() and loaded.exception() is None:
            # Shuts madmom's worker pools down. The desktop app stops them at once,
            # so quitting never waits for a half-finished analysis.
            loaded.result().close(force=desktop is not None)

    app = FastAPI(
        title="ChordChart", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])
    jobs: dict[str, Job] = {}
    transcriptions: dict = {}  # job id -> Transcription, for the MIDI download
    lock = threading.Lock()
    pending = [0]  # jobs submitted but not finished; a list so run() can update it
    app.state.is_busy = lambda: pending[0] > 0
    app.state.desktop = desktop

    def run(job: Job, work: Callable) -> None:
        """Run `work(status)`, which stores its result in `job`; then mark it done."""
        with lock:  # our turn: stop the "waiting for the previous analysis" spinner
            for entry in job.messages:
                entry["running"] = False

        def status(message: str, elapsed: float | None = None, started: bool = False) -> None:
            with lock:
                if elapsed is not None:
                    # Stages overlap: finish the running entry with this label.
                    for entry in reversed(job.messages):
                        if entry["text"] == message and entry["running"]:
                            entry["seconds"], entry["running"] = round(elapsed, 2), False
                            return
                job.messages.append(
                    {
                        "text": message,
                        "seconds": None if elapsed is None else round(elapsed, 2),
                        "running": started,
                    }
                )

        try:
            work(status)
            with lock:
                job.state = "done"
        except ChordChartError as exc:
            with lock:
                job.error, job.state = str(exc), "error"
        except Exception as exc:  # show it on the page, and keep the server alive
            log.exception("job failed")
            with lock:
                job.error = f"internal error: {type(exc).__name__}: {exc} (details in the terminal)"
                job.state = "error"
        finally:
            with lock:
                pending[0] -= 1

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    def submit(work: Callable) -> JSONResponse:
        job_id = uuid.uuid4().hex
        job = Job()
        with lock:
            if pending[0]:
                job.messages.append({"text": QUEUED, "seconds": None, "running": True})
            pending[0] += 1
            jobs[job_id] = job
        worker.submit(run, job, partial(work, job_id, job))
        return JSONResponse({"job_id": job_id})

    @app.post("/api/analyze")
    async def start_analysis(request: Request) -> JSONResponse:
        parsed = await _read_request(request)
        if isinstance(parsed, JSONResponse):
            return parsed
        source, start, end, _ = parsed

        def work(job_id: str, job: Job, status: Callable) -> None:
            song = analyze_fn(source, start=start, end=end, status=status)
            chart, data = render_text(song), json.loads(song.to_json())
            with lock:
                job.chart, job.song = chart, data

        return submit(work)

    @app.post("/api/notes")
    async def start_notes(request: Request) -> JSONResponse:
        parsed = await _read_request(request)
        if isinstance(parsed, JSONResponse):
            return parsed
        source, start, end, body = parsed
        instrument = str(body.get("instrument") or "auto")
        if instrument != "auto" and instrument not in INSTRUMENTS:
            return _error(f"instrument must be auto or one of {', '.join(INSTRUMENTS)}", 400)

        def work(job_id: str, job: Job, status: Callable) -> None:
            def progress(fraction: float) -> None:
                with lock:
                    job.progress = round(fraction, 3)

            result = notes_fn(
                source,
                start=start,
                end=end,
                instrument=None if instrument == "auto" else instrument,
                status=status,
                progress=progress,
            )
            data = json.loads(result.to_json())
            with lock:
                transcriptions[job_id] = result
                job.notes = data

        return submit(work)

    @app.get("/api/notes/{job_id}.mid")
    def notes_midi(job_id: str) -> Response:
        from chordchart.notes.midi import to_midi

        with lock:
            result = transcriptions.get(job_id)
        if result is None:
            return _error("no notes for this job", 404)
        name = f"{result.title} ({result.instrument}).mid"
        return Response(
            to_midi(result),
            media_type="audio/midi",
            headers={"Content-Disposition": _attachment(name)},
        )

    @app.get("/api/app")
    def app_info() -> JSONResponse:
        if desktop is None:
            return JSONResponse({"desktop": False})
        desktop.last_seen = time.monotonic()
        return JSONResponse({"desktop": True, "version": desktop.version})

    if desktop is not None:

        @app.get("/api/ping")
        def ping() -> JSONResponse:
            desktop.last_seen = time.monotonic()
            return JSONResponse({"ok": True})

        @app.get("/licenses", response_class=PlainTextResponse)
        def licenses() -> str:
            """The About line's link: every bundled component and its license."""
            from chordchart import bundled

            folder = bundled.bundle_dir()
            candidates = [folder / "licenses"] if folder else []
            candidates.append(Path(__file__).resolve().parents[2] / "packaging/build/licenses")
            for candidate in candidates:
                notices = candidate / "THIRD-PARTY-NOTICES.txt"
                if notices.is_file():
                    return notices.read_text(encoding="utf-8")
            return "License notices are included in the installed app (licenses folder)."

        @app.post("/api/quit")
        async def quit_app(request: Request) -> JSONResponse:
            # JSON-only, like /api/analyze: other websites can't send it cross-origin.
            content_type = request.headers.get("content-type", "").split(";")[0].strip()
            if content_type != "application/json":
                return _error("send JSON (Content-Type: application/json)", 415)
            threading.Timer(0.3, desktop.on_quit).start()  # let this response go out first
            return JSONResponse({"ok": True})

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> JSONResponse:
        with lock:
            job = jobs.get(job_id)
            data = None if job is None else asdict(job)
        if data is None:
            return _error("no such job", 404)
        return JSONResponse(data)

    return app


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    if not _port_is_free(port):
        print(
            f"error: port {port} is already in use; try: chordchart serve --port {port + 1}",
            file=sys.stderr,
        )
        return 2
    url = f"http://{HOST}:{port}/"
    print(f"ChordChart is running at {url}  (Ctrl+C to stop)", file=sys.stderr)
    if open_browser:
        _open_browser_soon(url)
    uvicorn.run(create_app(), host=HOST, port=port, log_level="warning")
    return 0


async def _read_request(request: Request):
    """(source, start, end, body) of a JSON analysis request, or an error response."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip()
    if content_type != "application/json":
        return _error("send JSON (Content-Type: application/json)", 415)
    try:
        body = await request.json()
    except ValueError:
        return _error("the request body is not valid JSON", 400)
    if not isinstance(body, dict):
        return _error("the request body must be a JSON object", 400)

    source = unquote(str(body.get("source") or ""))
    if not source:
        return _error("enter a link or a file path", 400)
    try:
        start = _time(body.get("start"), default=0.0)
        end = _time(body.get("end"), default=None)
    except ValueError as exc:
        return _error(str(exc), 400)
    if end is not None and end <= start:
        return _error("the end must be after the start", 400)
    return source, start, end, body


def _attachment(name: str) -> str:
    """Content-Disposition for a download, keeping a non-ASCII title (RFC 6266)."""
    plain = name.encode("ascii", "replace").decode().replace('"', "'")
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"


def _time(value: object, default: float | None) -> float | None:
    text = "" if value is None else str(value).strip()
    return default if not text else parse_time(text)


def _error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def _open_browser_soon(url: str) -> None:
    """Open the page once uvicorn has had a moment to start listening."""
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
