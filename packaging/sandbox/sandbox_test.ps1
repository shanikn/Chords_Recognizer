# Runs INSIDE Windows Sandbox (a clean Windows: no Python, ffmpeg or dev tools).
# Started by packaging/sandbox/run_sandbox_test.py, which maps two folders:
#   C:\sandbox\in   (read-only): the zip or installer to test
#   C:\sandbox\out  (writable) : this script and the results
# Writes results to C:\sandbox\out, then done.txt last.

$ErrorActionPreference = "Continue"
$in = "C:\sandbox\in"
$out = "C:\sandbox\out"
$log = Join-Path $out "sandbox-log.txt"
function Say($text) { "$(Get-Date -Format HH:mm:ss) $text" | Add-Content $log }

try {
    $mode = (Get-Content (Join-Path $out "mode.txt")).Trim()   # "zip" or "installer"
    # The variant: lite "ChordChart" or full "ChordChart Notes" (packaging/variants.py).
    $variant = Get-Content (Join-Path $out "variant.json") -Raw | ConvertFrom-Json
    $app = $variant.app_name
    $installDir = Join-Path $env:LOCALAPPDATA "Programs\$app"
    Say "mode: $mode; app: $app"
    if ($mode -eq "installer") {
        $setup = Get-ChildItem $in -Filter "$($variant.file_stem)-Setup-*.exe" | Select-Object -First 1
        Say "installing $($setup.Name) silently"
        Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$out\install-log.txt" -Wait
        $exe = Join-Path $installDir "ChordChart.exe"
        $desktopLink = Test-Path (Join-Path ([Environment]::GetFolderPath("Desktop")) "$app.lnk")
        $startLink = Test-Path (Join-Path ([Environment]::GetFolderPath("Programs")) "$app\$app.lnk")
        Say "installed exe exists: $(Test-Path $exe); desktop shortcut: $desktopLink; start menu shortcut: $startLink"
        "exe=$(Test-Path $exe) desktop=$desktopLink startmenu=$startLink" | Set-Content (Join-Path $out "install.txt")
        Get-ChildItem (Join-Path $installDir "licenses") -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty Name | Set-Content (Join-Path $out "licenses.txt")
    } else {
        $zip = Get-ChildItem $in -Filter "$($variant.file_stem)-*-win64.zip" | Select-Object -First 1
        Say "unzipping $($zip.Name)"
        Expand-Archive $zip.FullName -DestinationPath "C:\ChordChartTest" -Force
        $exe = Join-Path "C:\ChordChartTest\$app" "ChordChart.exe"
    }

    Say "python on PATH: $([bool](Get-Command python -ErrorAction SilentlyContinue)); ffmpeg on PATH: $([bool](Get-Command ffmpeg -ErrorAction SilentlyContinue))"

    Say "self-test"
    Start-Process $exe -ArgumentList "--self-test", "$out\selftest.json" -Wait

    Say "starting the app (no browser)"
    $env:CHORDCHART_IDLE_EXIT = "60"
    Start-Process $exe -ArgumentList "--no-browser"
    $appLog = Join-Path $env:LOCALAPPDATA "ChordChart\logs\$($variant.log_file)"
    $url = $null
    foreach ($i in 1..90) {
        Start-Sleep 1
        if (Test-Path $appLog) {
            $m = Select-String -Path $appLog -Pattern "running at (http://127\.0\.0\.1:\d+/)" | Select-Object -Last 1
            if ($m) { $url = $m.Matches[0].Groups[1].Value; break }
        }
    }
    Say "app url: $url"
    Invoke-RestMethod "${url}api/app" | ConvertTo-Json | Set-Content (Join-Path $out "app.json")

    Say "analysing a YouTube link"
    $body = '{"source":"https://www.youtube.com/watch?v=2eZVbrO6Z1M","start":"0:20","end":"1:00"}'
    $job = Invoke-RestMethod "${url}api/analyze" -Method Post -ContentType "application/json" -Body $body
    foreach ($i in 1..300) {
        Start-Sleep 1
        $state = Invoke-RestMethod "${url}api/jobs/$($job.job_id)"
        if ($state.state -ne "running") { break }
        if ($i % 30 -eq 0) { Invoke-RestMethod "${url}api/ping" | Out-Null }
    }
    $state | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $out "youtube.json")
    Say "analysis state: $($state.state)"

    $before = @(Get-Process ChordChart -ErrorAction SilentlyContinue).Count
    Say "quitting (processes before: $before)"
    Invoke-RestMethod "${url}api/quit" -Method Post -ContentType "application/json" -Body "{}" | Out-Null
    Start-Sleep 10
    $after = @(Get-Process ChordChart -ErrorAction SilentlyContinue).Count
    "before=$before after=$after" | Set-Content (Join-Path $out "processes.txt")
    Say "processes after quit: $after"

    Say "idle exit: start again, never open a page, expect exit after ~60 s"
    Start-Process $exe -ArgumentList "--no-browser"
    Start-Sleep 20
    $running = @(Get-Process ChordChart -ErrorAction SilentlyContinue).Count
    Start-Sleep 70
    $afterIdle = @(Get-Process ChordChart -ErrorAction SilentlyContinue).Count
    "running_at_20s=$running after_90s=$afterIdle" | Set-Content (Join-Path $out "idle.txt")
    Say "idle: running at 20 s: $running, after 90 s: $afterIdle"

    if ($mode -eq "installer") {
        $uninstaller = Join-Path $installDir "unins000.exe"
        Say "uninstalling"
        Start-Process $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait
        Start-Sleep 5
        $gone = -not (Test-Path (Join-Path $installDir "ChordChart.exe"))
        $desktopLink = Test-Path (Join-Path ([Environment]::GetFolderPath("Desktop")) "$app.lnk")
        "app_removed=$gone desktop_shortcut_left=$desktopLink" | Set-Content (Join-Path $out "uninstall.txt")
        Say "uninstalled: $gone; desktop shortcut left: $desktopLink"
    }
    Copy-Item $appLog (Join-Path $out "app-log.txt") -ErrorAction SilentlyContinue
} catch {
    Say "ERROR: $_"
} finally {
    Say "done"
    "done" | Set-Content (Join-Path $out "done.txt")
}
