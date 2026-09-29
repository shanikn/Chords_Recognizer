"""Test the packaged app on a clean Windows, in Windows Sandbox.

    uv run python packaging/sandbox/run_sandbox_test.py zip        # the portable zip
    uv run python packaging/sandbox/run_sandbox_test.py installer  # the Setup .exe
    ... --variant full    # "ChordChart Notes" instead of the lite "ChordChart"
    uv run python packaging/sandbox/run_sandbox_test.py both       # both installers side by
                                                                   # side, then uninstall each

Starts a Sandbox with the build output mapped read-only, runs sandbox_test.ps1 in it
(self-test; a real YouTube analysis and its cached repeat; a Spotify track link; a local
file through the upload; notes with MIDI and MusicXML in the Notes edition; Quit; idle
exit; the app's window and a second launch; for the installer also shortcuts, licenses
and uninstall), waits for its results, closes the Sandbox and prints a summary. "both"
installs the two editions side by side and checks that uninstalling either one keeps the
shared data folder. Only this version's builds are tested (out/ may hold older ones).
The variant's names (install folder, zip folder, log file) reach the script through
variant.json; the summary also checks the app reports notes exactly when it should.
Needs Windows 10/11 Pro with the "Windows Sandbox" feature enabled.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import variants  # noqa: E402

OUT = HERE.parent / "out"
RESULTS = HERE.parent / "build" / "sandbox-results"

COMMAND = r"powershell -NoProfile -ExecutionPolicy Bypass -File C:\sandbox\out\sandbox_test.ps1"
WSB = r"""<Configuration>
  <Networking>Enable</Networking>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>{inbox}</HostFolder>
      <SandboxFolder>C:\sandbox\in</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>{results}</HostFolder>
      <SandboxFolder>C:\sandbox\out</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>{command}</Command>
  </LogonCommand>
</Configuration>
"""


def main(mode: str, variant: variants.Variant) -> int:
    sys.path.insert(0, str(HERE.parents[1]))
    from chordchart import __version__
    from chordchart.desktop.app import instance_name

    if mode == "both":
        wanted = [f"{v.file_stem}-Setup-{__version__}.exe" for v in variants.VARIANTS.values()]
    elif mode == "installer":
        wanted = [f"{variant.file_stem}-Setup-{__version__}.exe"]
    else:
        wanted = [f"{variant.file_stem}-{__version__}-win64.zip"]
    artifacts = [OUT / name for name in wanted]
    if missing := [a.name for a in artifacts if not a.exists()]:
        raise SystemExit(f"nothing to test: no {', '.join(missing)} in {OUT}")
    inbox = HERE.parent / "build" / "sandbox-in"
    _close_sandbox()  # a Sandbox left over from an earlier run keeps these folders busy
    time.sleep(3)
    for folder in (inbox, RESULTS):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        for leftover in folder.iterdir():  # whatever rmtree couldn't remove
            leftover.unlink(missing_ok=True) if leftover.is_file() else None
    for artifact in artifacts:
        shutil.copy2(artifact, inbox / artifact.name)
    shutil.copy2(HERE / "sandbox_test.ps1", RESULTS / "sandbox_test.ps1")
    (RESULTS / "mode.txt").write_text(mode)
    names = {
        "app_name": variant.app_name,
        "file_stem": variant.file_stem,
        "log_file": variant.log_file,
        "instance": instance_name(variant.notes),  # names the app's .port file
    }
    (RESULTS / "variant.json").write_text(json.dumps(names))
    wsb = HERE.parent / "build" / "chordchart-test.wsb"
    config = WSB.format(inbox=inbox, results=RESULTS, command=COMMAND)
    wsb.write_text(config, encoding="utf-8")

    print(f"testing {', '.join(a.name for a in artifacts)} in Windows Sandbox...", flush=True)
    subprocess.run(["cmd", "/c", "start", "", str(wsb)], check=True)
    began = time.monotonic()
    while not (RESULTS / "done.txt").exists():
        if time.monotonic() - began > 60 * 60:  # notes: Demucs download + separation
            print("timed out after 60 minutes")
            break
        time.sleep(10)
    _close_sandbox()
    return _summary_both() if mode == "both" else _summary(variant)


def _close_sandbox() -> None:
    for name in (
        "WindowsSandboxRemoteSession.exe",
        "WindowsSandboxServer.exe",
        "WindowsSandboxClient.exe",
        "WindowsSandbox.exe",
    ):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)


def read(name):
    path = RESULTS / name
    return path.read_text(encoding="utf-8-sig", errors="replace").strip() if path.exists() else None


def _summary_both() -> int:
    print((read("sandbox-log.txt") or "(no log)") + "\n")
    both = read("both.txt") or ""
    print(f"both.txt: {both}")
    ok = both == (
        "lite=True notes=True kept_after_notes_uninstall=True reason1=True "
        "kept_after_lite_uninstall=True reason2=True"
    )
    for name in ("selftest-lite.json", "selftest-full.json"):
        report = json.loads(read(name) or '{"ok": false}')
        print(f"{name}: ok={report['ok']}")
        ok &= report["ok"]
    print(f"\nSANDBOX TEST {'PASSED' if ok else 'FAILED'} (results in {RESULTS})")
    return 0 if ok else 1


def _summary(variant: variants.Variant) -> int:
    ok = True
    print((read("sandbox-log.txt") or "(no log)") + "\n")
    selftest = json.loads(read("selftest.json") or '{"ok": false, "checks": {}}')
    print(f"self-test ok: {selftest['ok']}")
    for name, check in selftest["checks"].items():
        print(f"  {name}: {check['ok']} ({check['seconds']} s) {check['detail']}")
    ok &= selftest["ok"]
    youtube = json.loads(read("youtube.json") or "{}")
    print(f"YouTube analysis: {youtube.get('state')}  {youtube.get('error') or ''}")
    if youtube.get("chart"):
        print(youtube["chart"])
    ok &= youtube.get("state") == "done"
    spotify = json.loads(read("spotify.json") or "{}")
    video = ((spotify.get("song") or {}).get("match") or {}).get("video") or {}
    print(f"Spotify link: {spotify.get('state')} {spotify.get('error') or ''}"
          f"-> {video.get('title')} ({video.get('channel')})")  # fmt: skip
    ok &= spotify.get("state") == "done" and bool(video)
    local = json.loads(read("localfile.json") or "{}")
    title = (local.get("song") or {}).get("title")
    print(f"local file (upload): {local.get('state')} {local.get('error') or ''} title={title}")
    ok &= local.get("state") == "done" and title == "My Song"
    ok &= (read("cache.txt") or "").startswith("state=done cached=True")
    app = json.loads(read("app.json") or "{}")
    print(f"{variant.app_name}: /api/app notes = {app.get('notes')} (expected {variant.notes})")
    ok &= app.get("notes") is variant.notes
    names = ("cache.txt", "notes.txt", "window.txt", "processes.txt", "idle.txt", "install.txt")
    for name in (*names, "licenses.txt", "uninstall.txt"):
        if (text := read(name)) is not None:
            print(f"{name}: {text}")
    ok &= (read("processes.txt") or "").endswith("after=0")
    ok &= (read("idle.txt") or "").endswith("after_90s=0")
    window = read("window.txt") or ""
    ok &= window.startswith("windows=1 window_mode=True second_exited=True windows_after_second=1")
    ok &= window.endswith("after_quit=0")
    if variant.notes:
        notes = dict(p.split("=", 1) for p in (read("notes.txt") or "").split() if "=" in p)
        ok &= notes.get("state") == "done" and int(notes.get("notes") or 0) > 0
        ok &= int(notes.get("midi_bytes") or 0) > 0 and int(notes.get("musicxml_bytes") or 0) > 0
    print(f"\nSANDBOX TEST {'PASSED' if ok else 'FAILED'} (results in {RESULTS})")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the packaged app in Windows Sandbox.")
    parser.add_argument("mode", nargs="?", choices=["zip", "installer", "both"], default="zip")
    parser.add_argument("--variant", choices=sorted(variants.VARIANTS), default=variants.DEFAULT)
    args = parser.parse_args()
    sys.exit(main(args.mode, variants.VARIANTS[args.variant]))
