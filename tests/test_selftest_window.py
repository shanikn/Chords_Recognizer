"""chordchart/desktop/selftest.py: the window check fails if part of the window's
runtime is missing from the bundle (lite once shipped without cffi)."""

import sys

import pytest

from chordchart.desktop import selftest

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="the window is Windows-only")


@windows_only
def test_the_window_runtime_loads():
    detail = selftest._window()
    assert {"cffi", "clr_loader", "pythonnet", "clr", "webview.platforms.edgechromium"} <= set(
        detail["imported"]
    )


@windows_only
@pytest.mark.parametrize("missing", ["cffi", "clr_loader", "pythonnet"])
def test_a_missing_package_fails_the_check(monkeypatch, missing):
    monkeypatch.setitem(sys.modules, missing, None)  # makes `import <missing>` fail
    with pytest.raises(ImportError):
        selftest._window()
