"""ChordChart's own window: the page in Microsoft Edge WebView2, via pywebview.

WebView2 comes with Windows 11 and current Windows 10. Without it (or without
pywebview), `available()` is False and the app opens the page in the default browser
instead, exactly as before; so a missing runtime never stops the app.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

# WebView2 Runtime's registration (Microsoft's documented detection keys).
_WEBVIEW2_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def webview2_version() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    for root, prefix in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node"),
        (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE"),
        (winreg.HKEY_CURRENT_USER, "SOFTWARE"),
    ):
        try:
            with winreg.OpenKey(root, rf"{prefix}\{_WEBVIEW2_CLIENT}") as key:
                version, _ = winreg.QueryValueEx(key, "pv")
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return str(version)
    return None


def available() -> bool:
    if webview2_version() is None:
        log.info("WebView2 runtime not found: using the browser")
        return False
    try:
        import webview  # noqa: F401
    except Exception as exc:  # a broken bundle must not stop the app
        log.warning("pywebview unavailable (%s): using the browser", exc)
        return False
    return True


class Window:
    """The app window. `run()` blocks the main thread until the window closes."""

    def __init__(self, url: str, title: str, storage: str) -> None:
        import webview

        # MIDI/MusicXML downloads; links with target=_blank (YouTube, Licenses) open
        # in the default browser.
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        self._webview = webview
        self._storage = storage
        self.window = webview.create_window(
            title, url, width=1100, height=860, min_size=(560, 480), text_select=True
        )
        self.closed = threading.Event()
        self.window.events.closed += self.closed.set

    def run(self, on_start: Callable[[], None] | None = None) -> None:
        self._webview.start(
            on_start, gui="edgechromium", private_mode=False, storage_path=self._storage
        )  # fmt: skip

    def show(self) -> None:
        """Bring the window to the front (a second launch of the app asks for this)."""
        try:
            self.window.restore()
            self.window.show()
            # Windows won't let a background process steal focus; flashing "on top"
            # raises the window without keeping it there.
            self.window.on_top = True
            self.window.on_top = False
        except Exception:
            log.exception("couldn't bring the window to the front")

    def close(self) -> None:
        try:
            self.window.destroy()
        except Exception:
            log.exception("couldn't close the window")
