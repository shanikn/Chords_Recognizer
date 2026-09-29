"""chordchart/desktop/instance.py and window.py: one app per variant, workers die with
the app, and the browser fallback when there is no WebView2."""

import subprocess
import sys
import textwrap
import time
import uuid

import pytest

from chordchart.desktop import instance, window

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows API")


@windows_only
def test_a_second_claim_of_the_same_name_fails():
    name = f"ChordChartTest-{uuid.uuid4().hex}"
    assert instance.claim(name)
    assert not instance.claim(name)
    assert instance.claim(name + "-other")  # the other variant runs independently


def test_show_running_posts_to_the_recorded_port(tmp_path, monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        seen.append((request.full_url, request.get_method(), request.headers["Content-type"]))
        return Response()

    monkeypatch.setattr(instance.urllib.request, "urlopen", urlopen)
    instance.record_port(tmp_path, "ChordChart", 51234)
    assert instance.show_running(tmp_path, "ChordChart") == 51234
    assert seen == [("http://127.0.0.1:51234/api/show", "POST", "application/json")]


def test_show_running_without_a_port_file(tmp_path):
    assert instance.show_running(tmp_path, "ChordChart") is None


@windows_only
def test_children_die_with_the_app():
    # A parent in the Job Object starts a long-lived child, then is killed outright
    # (as by a crash or Task Manager): the child must go too.
    parent = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent("""
            import subprocess, sys, time
            from chordchart.desktop import instance
            assert instance.kill_children_on_exit()
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            print(child.pid, flush=True)
            time.sleep(60)
        """)],
        stdout=subprocess.PIPE,
        text=True,
    )  # fmt: skip
    child_pid = int(parent.stdout.readline())
    parent.kill()
    parent.wait()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _alive(child_pid):
        time.sleep(0.1)
    assert not _alive(child_pid)


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def test_no_webview2_means_the_browser(monkeypatch):
    monkeypatch.setattr(window, "webview2_version", lambda: None)
    assert not window.available()


def test_instance_names_differ_per_variant():
    from chordchart.desktop.app import instance_name

    assert instance_name(notes=True) != instance_name(notes=False)
