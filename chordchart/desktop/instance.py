"""One running app per variant, and no orphaned worker processes (Windows only).

- `claim(name)` takes a named mutex. The first launch owns it and records its port in
  `<app data>/<name>.port`; a second launch (double-clicking the shortcut again) finds
  the mutex taken, asks the running app to come to the front (POST /api/show), and exits.
  The lite and Notes apps use different names, so both can run at once.
- `kill_children_on_exit()` puts the app in a Job Object that kills every process in it
  when the app's last handle closes, including when the app crashes or is ended in Task
  Manager. The beat tracker's worker processes, ffmpeg, deno and yt-dlp's helpers are
  started by the app, so they're in the job too.
"""

from __future__ import annotations

import ctypes
import json
import logging
import sys
import urllib.request
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183
_handles: list = []  # kept open for the life of the process


def mutex_name(name: str) -> str:
    return f"Local\\{name}-single-instance"


def claim(name: str) -> bool:
    """True if this is the only running app called `name` (it now owns the name)."""
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, mutex_name(name))
    if not handle:
        return True  # can't tell: better two windows than none
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _handles.append(handle)
    return True


def port_file(data_dir: Path, name: str) -> Path:
    return data_dir / f"{name}.port"


def record_port(data_dir: Path, name: str, port: int) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    port_file(data_dir, name).write_text(json.dumps({"port": port}), encoding="utf-8")


def show_running(data_dir: Path, name: str, timeout: float = 5.0) -> int | None:
    """Ask the running app to show itself; its port, or None if it didn't answer."""
    try:
        port = int(json.loads(port_file(data_dir, name).read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/show",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310 (localhost)
            return port
    except OSError:
        return None


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]  # fmt: skip


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


def kill_children_on_exit() -> bool:
    """Put this process (and so everything it starts) in a kill-on-close Job Object."""
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        log.warning("no Job Object (error %d)", ctypes.get_last_error())
        return False
    info = _ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    ) or not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
        log.warning("couldn't set up the Job Object (error %d)", ctypes.get_last_error())
        kernel32.CloseHandle(job)
        return False
    _handles.append(job)  # closed by Windows when the process ends: that kills the job
    return True
