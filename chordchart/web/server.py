"""`chordchart serve`: a small local web UI over the same pipeline as the CLI.

    GET  /                 the page (index.html next to this file)
    POST /api/analyze      {"source", "start", "end", "video"} -> {"job_id"}; "video" (optional,
                           Spotify sources only) is a YouTube link to use instead of the
                           automatic match, remembered for next time
    GET  /api/jobs/{id}    {"state": running|done|error, "messages", "chart", "song", "error"}
    POST /api/upload?name=song.mp3   the file's bytes (application/octet-stream) ->
                           {"path"}: a local copy to analyse, for the page's "Choose file"
    POST /api/notes        {"source", "start", "end", "instrument"} -> {"job_id"}; the job's
                           "notes" is the Transcription, "progress" the stem separation's
    GET  /api/notes/{id}.mid   that job's notes as a MIDI file
    GET  /api/notes/{id}.musicxml   ... as sheet music (MusicXML, piano grand staff)
    GET  /vendor/{file}    bundled JavaScript (the sheet music renderer)
    GET  /api/app          {"desktop", "version", "notes", "window"}: "notes" false = not
                           installed; "window" true = in the app's own window (no Quit button)
    POST /api/show         desktop app: bring the window (or a browser tab) to the front
    POST /api/update-ytdlp desktop app: install the newest yt-dlp ("Update YouTube support")

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
import os
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

from chordchart import spotify
from chordchart.download import default_cache_dir
from chordchart.errors import ChordChartError
from chordchart.interactive import unquote
from chordchart.notes.available import MISSING as NOTES_MISSING
from chordchart.notes.available import notes_available
from chordchart.notes.model import INSTRUMENTS
from chordchart.render.text import render_text
from chordchart.timecode import parse_time

log = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PAGE = Path(__file__).with_name("index.html")
VENDOR = Path(__file__).with_name("vendor")
VENDOR_FILES = {"opensheetmusicdisplay.min.js": "text/javascript"}
MUSICXML_TYPE = "application/vnd.recordare.musicxml+xml"
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
    on_show: Callable[[], None] | None = None  # a second launch of the app calls this
    window: bool = False  # the page is in the app's own window


def create_app(
    analyze_fn: Callable | None = None,
    desktop: Desktop | None = None,
    notes_fn: Callable | None = None,
    notes: bool | None = None,
) -> FastAPI:
    """The app. `analyze_fn` defaults to pipeline.analyze with one set of processors
    kept loaded for the server's lifetime; tests pass a fake instead. `notes_fn`
    defaults to notes.pipeline.transcribe_notes using that same `analyze_fn`. `notes`
    says whether the notes feature is installed (detected once, here, by default; the
    lite desktop app leaves it out). `desktop` adds the desktop app's quit button and
    heartbeat."""
    if notes is None:
        notes = notes_available()
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
        source, start, end, body = parsed

        def work(job_id: str, job: Job, status: Callable) -> None:
            _use_video(source, body, status)
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
        if not notes:
            return _error(NOTES_MISSING, 404)
        instrument = str(body.get("instrument") or "auto")
        if instrument != "auto" and instrument not in INSTRUMENTS:
            return _error(f"instrument must be auto or one of {', '.join(INSTRUMENTS)}", 400)

        def work(job_id: str, job: Job, status: Callable) -> None:
            _use_video(source, body, status)

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

    def notes_file(job_id: str, extension: str, media_type: str, render: Callable) -> Response:
        with lock:
            result = transcriptions.get(job_id)
        if result is None:
            return _error("no notes for this job", 404)
        name = f"{result.title} ({result.instrument}).{extension}"
        return Response(
            render(result),
            media_type=media_type,
            headers={"Content-Disposition": _attachment(name)},
        )

    @app.get("/api/notes/{job_id}.mid")
    def notes_midi(job_id: str) -> Response:
        from chordchart.notes.midi import to_midi

        return notes_file(job_id, "mid", "audio/midi", to_midi)

    @app.get("/api/notes/{job_id}.musicxml")
    def notes_musicxml(job_id: str) -> Response:
        from chordchart.notes.musicxml import to_musicxml

        return notes_file(job_id, "musicxml", MUSICXML_TYPE, to_musicxml)

    @app.get("/vendor/{name}")
    def vendor(name: str) -> Response:
        """Bundled JavaScript (web/vendor), so the page never needs a CDN. Only listed
        files are served; the lite desktop app doesn't bundle them (no notes there)."""
        path = VENDOR / name
        if name not in VENDOR_FILES or not path.is_file():
            return _error("not found", 404)
        return Response(path.read_bytes(), media_type=VENDOR_FILES[name])

    @app.get("/api/app")
    def app_info() -> JSONResponse:
        if desktop is None:
            return JSONResponse({"desktop": False, "notes": notes})
        desktop.last_seen = time.monotonic()
        return JSONResponse(
            {"desktop": True, "version": desktop.version, "notes": notes, "window": desktop.window}
        )

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
            build = Path(__file__).resolve().parents[2] / "packaging" / "build"
            candidates += [build / "full" / "licenses", build / "lite" / "licenses"]
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

        @app.post("/api/show")
        async def show(request: Request) -> JSONResponse:
            content_type = request.headers.get("content-type", "").split(";")[0].strip()
            if content_type != "application/json":
                return _error("send JSON (Content-Type: application/json)", 415)
            if desktop.on_show is not None:
                threading.Thread(target=desktop.on_show, daemon=True).start()
            return JSONResponse({"ok": True})

        @app.post("/api/update-ytdlp")
        def update_ytdlp(request: Request) -> JSONResponse:
            # Sync handler (runs in a worker thread): the update downloads and checks
            # a few MB. JSON-only like the other POSTs.
            content_type = request.headers.get("content-type", "").split(";")[0].strip()
            if content_type != "application/json":
                return _error("send JSON (Content-Type: application/json)", 415)
            from yt_dlp.version import __version__ as current

            from chordchart.desktop import ytdlp_update

            try:
                result = ytdlp_update.update(current)
            except ytdlp_update.UpdateError as exc:
                return _error(str(exc), 502)
            return JSONResponse(asdict(result))

    @app.post("/api/upload")
    async def upload(request: Request, name: str = "audio") -> JSONResponse:
        # octet-stream, like JSON, can't be sent cross-origin without a preflight.
        content_type = request.headers.get("content-type", "").split(";")[0].strip()
        if content_type != "application/octet-stream":
            return _error("send the file as application/octet-stream", 415)
        try:
            path = await _save_upload(request, name, default_cache_dir() / "uploads")
        except ValueError as exc:
            return _error(str(exc), 413)
        return JSONResponse({"path": str(path)})

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
    if body.get("video") and not spotify.is_spotify(source):
        return _error("a replacement video can only be given for a Spotify link", 400)
    return source, start, end, body


UPLOAD_LIMIT = 1024**3  # bytes; a long WAV is a few hundred MB
UPLOAD_KEEP = 7 * 24 * 3600  # seconds an uploaded copy is kept


async def _save_upload(request: Request, name: str, folder: Path) -> Path:
    """Stream the upload to <folder>/<content hash>/<name>: the page shows the file's own
    name as the title, and the same file uploaded twice is stored once. Copies older
    than UPLOAD_KEEP are deleted (the analysis cache keeps their results)."""
    import hashlib
    import re
    import shutil

    folder.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in folder.iterdir():
        if old.is_dir() and now - old.stat().st_mtime > UPLOAD_KEEP:
            shutil.rmtree(old, ignore_errors=True)
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", Path(name).name).strip(" .") or "audio"
    digest = hashlib.sha256()
    tmp = folder / f"upload-{uuid.uuid4().hex}.part"
    size = 0
    try:
        with tmp.open("wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > UPLOAD_LIMIT:
                    raise ValueError(f"the file is over {UPLOAD_LIMIT // 1024**2} MB")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise ValueError("the file is empty")
        target = folder / digest.hexdigest()[:16] / safe
        target.parent.mkdir(exist_ok=True)
        tmp.replace(target)
        os.utime(target.parent)  # recently used: not pruned
        return target
    finally:
        tmp.unlink(missing_ok=True)


def _use_video(source: str, body: dict, status: Callable) -> None:
    """Record the user's own YouTube video for a Spotify track (the analysis then uses it)."""
    if video := str(body.get("video") or "").strip():
        spotify.match_track(source, default_cache_dir() / "downloads", video_link=video, status=status)


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
