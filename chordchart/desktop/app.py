"""The Windows desktop app: double-click -> ChordChart opens in the browser.

Entry point of the packaged ChordChart.exe (PyInstaller, no console window):

1. `multiprocessing.freeze_support()` first. The beat tracker's worker processes are
   started by re-running ChordChart.exe; this line turns those runs into workers
   instead of starting a second app.
2. With no console, stdout/stderr go to a log file in %LOCALAPPDATA%\\ChordChart\\logs.
3. A newer yt-dlp from the updater is used if one is installed (ytdlp_update.activate).
4. The server starts on a free local port (127.0.0.1 only) and the default browser
   opens the page.
5. It exits when the page's "Quit ChordChart" button is pressed, or when no page has
   pinged for IDLE_EXIT seconds (and nothing is being analysed). Shutdown stops the
   server and terminates the beat-tracking workers.

Round 2 (not yet): own window (pywebview), single instance, Windows Job Object.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys
import threading
import time
import webbrowser

# Seconds with no open page before exiting (the page pings every 20 s). The
# environment variable is for testing only.
IDLE_EXIT = int(os.environ.get("CHORDCHART_IDLE_EXIT", 5 * 60))
FRIENDLY_UPDATE_HINT = (
    "close ChordChart, wait a few days, and try again; if it keeps failing, ask for an "
    "updated ChordChart"
)


def main() -> int:
    multiprocessing.freeze_support()  # must run before anything else in a worker

    from chordchart import bundled

    bundled.register_dll_folder()  # before anything can import torch (full app)

    from chordchart.desktop import ytdlp_update

    if len(sys.argv) >= 4 and sys.argv[1] == ytdlp_update.CHECK_FLAG:
        return ytdlp_update.run_import_check(sys.argv[2], sys.argv[3])

    log_file = _setup_logging()
    ytdlp_update.activate()  # before anything imports yt_dlp

    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
        from chordchart.desktop.selftest import run

        return run(sys.argv[2] if len(sys.argv) >= 3 else "chordchart-selftest.json")

    return _run_app(log_file, open_browser="--no-browser" not in sys.argv)


def _run_app(log_file, open_browser: bool) -> int:
    import uvicorn

    from chordchart import __version__, bundled
    from chordchart.web import server

    bundled.UPDATE_HINT = FRIENDLY_UPDATE_HINT
    stop = threading.Event()
    desktop = server.Desktop(on_quit=stop.set, version=__version__)
    app = server.create_app(desktop=desktop)
    port = _free_port()
    config = uvicorn.Config(app, host=server.HOST, port=port, log_level="warning")
    web = uvicorn.Server(config)
    thread = threading.Thread(target=web.run, name="server", daemon=True)
    thread.start()

    url = f"http://{server.HOST}:{port}/"
    deadline = time.monotonic() + 30
    while not web.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not web.started:
        logging.error("the server didn't start; see %s", log_file)
        return 1
    logging.info("ChordChart %s running at %s", __version__, url)
    if open_browser:
        webbrowser.open(url)

    desktop.last_seen = time.monotonic()
    while not stop.wait(5):
        idle = time.monotonic() - desktop.last_seen
        if idle > IDLE_EXIT and not app.state.is_busy():
            logging.info("no page open for %d s; exiting", idle)
            break

    logging.info("shutting down")
    web.should_exit = True  # runs the app's shutdown: stops the beat-tracking workers
    thread.join(timeout=30)
    logging.info("stopped")
    logging.shutdown()
    # A half-finished analysis may still hold a (now idle) thread; don't wait for it.
    os._exit(0)


def log_name(notes: bool) -> str:
    """The log file's name: one per variant (packaging/variants.py), because both apps
    can run at once and share the logs folder. With one name, one would rename (rotate)
    the log the other has open, which fails on Windows."""
    return "chordchart-notes.log" if notes else "chordchart.log"


def _setup_logging():
    from chordchart import bundled
    from chordchart.notes.available import notes_available

    folder = bundled.app_data_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    log_file = folder / log_name(notes=notes_available())
    if log_file.exists() and log_file.stat().st_size > 2_000_000:
        log_file.replace(log_file.with_suffix(".old.log"))
    # A windowed app has no console: sys.stdout/stderr are None, and anything that
    # prints (uvicorn, our status lines) would crash. Send it all to the log.
    stream = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    logging.basicConfig(
        stream=stream, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    return log_file


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


if __name__ == "__main__":
    sys.exit(main())
