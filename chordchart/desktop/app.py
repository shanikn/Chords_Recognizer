"""The Windows desktop app: double-click -> ChordChart opens in its own window.

Entry point of the packaged ChordChart.exe (PyInstaller, no console window):

1. `multiprocessing.freeze_support()` first. The beat tracker's worker processes are
   started by re-running ChordChart.exe; this line turns those runs into workers
   instead of starting a second app.
2. With no console, stdout/stderr go to a log file in %LOCALAPPDATA%\\ChordChart\\logs.
3. A newer yt-dlp from the updater is used if one is installed (ytdlp_update.activate).
4. One app per variant (instance.claim): a second launch brings the running one to
   the front and exits. The app sits in a kill-on-close Job Object, so its worker
   processes can't outlive it, even after a crash.
5. The server starts on a free local port (127.0.0.1 only) and the page opens in the
   app's own window (window.py: WebView2). The app exits when the window is closed.
6. Without WebView2 (or with --browser), the default browser opens the page instead;
   the app then exits when the page's "Quit ChordChart" button is pressed, or when no
   page has pinged for IDLE_EXIT seconds (and nothing is being analysed).
Shutdown stops the server and terminates the beat-tracking workers.
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
    'press "Update YouTube support" at the bottom of the page, then close and reopen '
    "ChordChart; if it keeps failing, ask for an updated ChordChart"
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

    return _run_app(
        log_file,
        open_browser="--no-browser" not in sys.argv,
        allow_window="--browser" not in sys.argv,
    )


def instance_name(notes: bool) -> str:
    return "ChordChart-Notes" if notes else "ChordChart"


def _run_app(log_file, open_browser: bool, allow_window: bool = True) -> int:
    import uvicorn

    from chordchart import __version__, bundled
    from chordchart.desktop import instance
    from chordchart.desktop import window as app_window
    from chordchart.notes.available import notes_available
    from chordchart.web import server

    name, data_dir = instance_name(notes_available()), bundled.app_data_dir()
    if not instance.claim(name):
        port = instance.show_running(data_dir, name)
        logging.info("already running (port %s): showed it and exiting", port)
        return 0
    instance.kill_children_on_exit()

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
    instance.record_port(data_dir, name, port)

    if open_browser and allow_window and app_window.available():
        try:
            win = app_window.Window(url, "ChordChart", storage=str(data_dir / "webview"))
            desktop.on_quit, desktop.on_show, desktop.window = win.close, win.show, True
            win.run()  # until the window closes
            return _shutdown(web, thread)
        except Exception:
            logging.exception("the window failed; using the browser")
            desktop.on_quit, desktop.window = stop.set, False

    desktop.on_show = lambda: webbrowser.open(url)
    if open_browser:
        webbrowser.open(url)

    desktop.last_seen = time.monotonic()
    while not stop.wait(5):
        idle = time.monotonic() - desktop.last_seen
        if idle > IDLE_EXIT and not app.state.is_busy():
            logging.info("no page open for %d s; exiting", idle)
            break
    return _shutdown(web, thread)


def _shutdown(web, thread) -> int:
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
