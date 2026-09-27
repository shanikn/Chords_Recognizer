"""Test the packaged app on a clean Windows, in Windows Sandbox.

    uv run python packaging/sandbox/run_sandbox_test.py zip        # the portable zip
    uv run python packaging/sandbox/run_sandbox_test.py installer  # the Setup .exe
    ... --variant full    # "ChordChart Notes" instead of the lite "ChordChart"

Starts a Sandbox with the build output mapped read-only, runs sandbox_test.ps1 in it
(self-test, a real YouTube analysis, Quit, idle exit; for the installer also shortcuts,
licenses and uninstall), waits for its results, closes the Sandbox and prints a summary.
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
    stem = variant.file_stem
    pattern = f"{stem}-Setup-*.exe" if mode == "installer" else f"{stem}-*-win64.zip"
    artifact = next(OUT.glob(pattern), None)
    if artifact is None:
        raise SystemExit(f"nothing to test: no {pattern} in {OUT}")
    inbox = HERE.parent / "build" / "sandbox-in"
    _close_sandbox()  # a Sandbox left over from an earlier run keeps these folders busy
    time.sleep(3)
    for folder in (inbox, RESULTS):
        shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        for leftover in folder.iterdir():  # whatever rmtree couldn't remove
            leftover.unlink(missing_ok=True) if leftover.is_file() else None
    shutil.copy2(artifact, inbox / artifact.name)
    shutil.copy2(HERE / "sandbox_test.ps1", RESULTS / "sandbox_test.ps1")
    (RESULTS / "mode.txt").write_text(mode)
    names = {"app_name": variant.app_name, "file_stem": stem, "log_file": variant.log_file}
    (RESULTS / "variant.json").write_text(json.dumps(names))
    wsb = HERE.parent / "build" / "chordchart-test.wsb"
    config = WSB.format(inbox=inbox, results=RESULTS, command=COMMAND)
    wsb.write_text(config, encoding="utf-8")

    print(f"testing {artifact.name} in Windows Sandbox...", flush=True)
    subprocess.run(["cmd", "/c", "start", "", str(wsb)], check=True)
    began = time.monotonic()
    while not (RESULTS / "done.txt").exists():
        if time.monotonic() - began > 30 * 60:
            print("timed out after 30 minutes")
            break
        time.sleep(10)
    _close_sandbox()
    return _summary(variant)


def _close_sandbox() -> None:
    for name in (
        "WindowsSandboxRemoteSession.exe",
        "WindowsSandboxServer.exe",
        "WindowsSandboxClient.exe",
        "WindowsSandbox.exe",
    ):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)


def _summary(variant: variants.Variant) -> int:
    def read(name):
        path = RESULTS / name
        return (
            path.read_text(encoding="utf-8-sig", errors="replace").strip()
            if path.exists()
            else None
        )

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
    app = json.loads(read("app.json") or "{}")
    print(f"{variant.app_name}: /api/app notes = {app.get('notes')} (expected {variant.notes})")
    ok &= app.get("notes") is variant.notes
    for name in ("processes.txt", "idle.txt", "install.txt", "licenses.txt", "uninstall.txt"):
        if (text := read(name)) is not None:
            print(f"{name}: {text}")
    ok &= (read("processes.txt") or "").endswith("after=0")
    ok &= (read("idle.txt") or "").endswith("after_90s=0")
    print(f"\nSANDBOX TEST {'PASSED' if ok else 'FAILED'} (results in {RESULTS})")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the packaged app in Windows Sandbox.")
    parser.add_argument("mode", nargs="?", choices=["zip", "installer"], default="zip")
    parser.add_argument("--variant", choices=sorted(variants.VARIANTS), default=variants.DEFAULT)
    args = parser.parse_args()
    sys.exit(main(args.mode, variants.VARIANTS[args.variant]))
