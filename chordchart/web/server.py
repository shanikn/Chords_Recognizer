"""`chordchart serve`: a small local web UI over the same pipeline as the CLI.

    GET  /                 the page (index.html next to this file)
    POST /api/analyze      {"source", "start", "end"} -> {"job_id"}
    GET  /api/jobs/{id}    {"state": running|done|error, "messages", "chart", "song", "error"}

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
import uuid
import webbrowser
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from chordchart.errors import ChordChartError
from chordchart.interactive import unquote
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
    messages: list[str] = field(default_factory=list)
    chart: str | None = None  # render_text(song)
    song: dict | None = None  # Song JSON
    error: str | None = None


def create_app(analyze_fn: Callable | None = None) -> FastAPI:
    """The app. `analyze_fn` defaults to pipeline.analyze; tests pass a fake."""
    if analyze_fn is None:
        from chordchart.pipeline import analyze as analyze_fn

    app = FastAPI(title="ChordChart", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST, "localhost"])
    jobs: dict[str, Job] = {}
    lock = threading.Lock()
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chordchart-job")
    pending = [0]  # jobs submitted but not finished; a list so run() can update it

    def run(job: Job, source: str, start: float, end: float | None) -> None:
        def status(message: str) -> None:
            with lock:
                job.messages.append(message)

        try:
            song = analyze_fn(source, start=start, end=end, status=status)
            chart, data = render_text(song), json.loads(song.to_json())
            with lock:
                job.chart, job.song, job.state = chart, data, "done"
        except ChordChartError as exc:
            with lock:
                job.error, job.state = str(exc), "error"
        except Exception as exc:  # show it on the page, and keep the server alive
            log.exception("analysis of %s failed", source)
            with lock:
                job.error = f"internal error: {type(exc).__name__}: {exc} (details in the terminal)"
                job.state = "error"
        finally:
            with lock:
                pending[0] -= 1

    @app.get("/", response_class=HTMLResponse)
    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    @app.post("/api/analyze")
    async def start_analysis(request: Request) -> JSONResponse:
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

        job_id = uuid.uuid4().hex
        job = Job()
        with lock:
            if pending[0]:
                job.messages.append(QUEUED)
            pending[0] += 1
            jobs[job_id] = job
        worker.submit(run, job, source, start, end)
        return JSONResponse({"job_id": job_id})

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
