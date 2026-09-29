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

function WaitJob($url, $jobId, $seconds) {
    foreach ($i in 1..$seconds) {
        Start-Sleep 1
        $state = Invoke-RestMethod "${url}api/jobs/$jobId"
        if ($state.state -ne "running") { return $state }
        if ($i % 30 -eq 0) { Invoke-RestMethod "${url}api/ping" | Out-Null }
    }
    return $state
}
function AppCount() { @(Get-Process ChordChart -ErrorAction SilentlyContinue).Count }
function WindowCount() {
    @(Get-Process ChordChart -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "ChordChart*" }).Count
}

try {
    $mode = (Get-Content (Join-Path $out "mode.txt")).Trim()   # "zip", "installer" or "both"
    if ($mode -eq "both") {
        # Both editions side by side; the shared data folder must survive uninstalling
        # either one while the other is installed (installer.iss, OtherVariantInstalled).
        $data = Join-Path $env:LOCALAPPDATA "ChordChart"
        foreach ($stem in "ChordChart", "ChordChartNotes") {
            $setup = Get-ChildItem $in -Filter "$stem-Setup-*.exe" | Select-Object -First 1
            Say "installing $($setup.Name)"
            Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$out\install-$stem.txt" -Wait
        }
        $lite = Join-Path $env:LOCALAPPDATA "Programs\ChordChart\ChordChart.exe"
        $full = Join-Path $env:LOCALAPPDATA "Programs\ChordChart Notes\ChordChart.exe"
        $liteInstalled, $fullInstalled = (Test-Path $lite), (Test-Path $full)  # before uninstalling
        Say "both installed: lite $liteInstalled, notes $fullInstalled"
        Say "self-test of each (creates the shared data folder)"
        Start-Process $lite -ArgumentList "--self-test", "$out\selftest-lite.json" -Wait
        Start-Process $full -ArgumentList "--self-test", "$out\selftest-full.json" -Wait
        New-Item -ItemType Directory -Force (Join-Path $data "marker") | Out-Null
        Say "uninstalling ChordChart Notes (lite still installed)"
        Start-Process (Join-Path $env:LOCALAPPDATA "Programs\ChordChart Notes\unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$out\uninstall-notes.txt" -Wait
        Start-Sleep 5
        $keptAfterFirst = Test-Path (Join-Path $data "marker")
        $reason1 = [bool](Select-String -Path "$out\uninstall-notes.txt" -Pattern "Shared data kept: ChordChart is still installed" -Quiet)
        Say "uninstalling ChordChart (nothing else installed)"
        Start-Process (Join-Path $env:LOCALAPPDATA "Programs\ChordChart\unins000.exe") -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG=$out\uninstall-lite.txt" -Wait
        Start-Sleep 5
        $keptAfterSecond = Test-Path (Join-Path $data "marker")
        $reason2 = [bool](Select-String -Path "$out\uninstall-lite.txt" -Pattern "Shared data kept \(silent uninstall\)" -Quiet)
        "lite=$liteInstalled notes=$fullInstalled kept_after_notes_uninstall=$keptAfterFirst reason1=$reason1 kept_after_lite_uninstall=$keptAfterSecond reason2=$reason2" | Set-Content (Join-Path $out "both.txt")
        Say "shared data kept after uninstalling Notes: $keptAfterFirst ($reason1); after lite (silent): $keptAfterSecond ($reason2)"
        return
    }
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

    Say "the same section again (cache)"
    $began = Get-Date
    $job = Invoke-RestMethod "${url}api/analyze" -Method Post -ContentType "application/json" -Body $body
    $again = WaitJob $url $job.job_id 120
    $cached = [bool]($again.messages | Where-Object { $_.text -eq "using cached analysis" })
    "state=$($again.state) cached=$cached seconds=$([int]((Get-Date) - $began).TotalSeconds)" | Set-Content (Join-Path $out "cache.txt")
    Say "repeat: $($again.state), cached analysis: $cached"

    # Each check below runs on its own: a failure is recorded and the next one still runs.
    try {
        Say "a Spotify track link (matched on YouTube)"
        $spotifyBody = '{"source":"https://open.spotify.com/track/3n3Ppam7vgaVa1iaRUc9Lp","start":"0:30","end":"1:10"}'
        $job = Invoke-RestMethod "${url}api/analyze" -Method Post -ContentType "application/json" -Body $spotifyBody
        $spotify = WaitJob $url $job.job_id 300
        $spotify | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $out "spotify.json")
        Say "Spotify: $($spotify.state) $($spotify.error)"
    } catch { Say "Spotify check failed: $_" }

    try {
        Say "a local file through Choose file (upload)"
        $song = Get-ChildItem (Join-Path $env:LOCALAPPDATA "chordchart\cache\downloads") -Filter "youtube-2eZVbrO6Z1M.*" |
            Where-Object { $_.Extension -ne ".json" } | Select-Object -First 1
        Say "uploading $($song.Name) ($($song.Length) bytes)"
        # curl.exe (part of Windows 10/11) sends the file like the page's fetch() does.
        $response = & curl.exe -s -S -X POST -H "Content-Type: application/octet-stream" --data-binary "@$($song.FullName)" "${url}api/upload?name=My%20Song$($song.Extension)" 2>&1
        Say "upload response: $response"
        $upload = $response | ConvertFrom-Json
        $fileBody = @{ source = $upload.path; start = "0:20"; end = "1:00" } | ConvertTo-Json
        $job = Invoke-RestMethod "${url}api/analyze" -Method Post -ContentType "application/json" -Body $fileBody
        $local = WaitJob $url $job.job_id 300
        $local | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $out "localfile.json")
        Say "local file: $($local.state), title: $($local.song.title)"
    } catch { Say "local file check failed: $_" }

    $info = Invoke-RestMethod "${url}api/app"
    if ($info.notes) { try {
        Say "notes: transcribing the YouTube section (downloads the Demucs model first)"
        $job = Invoke-RestMethod "${url}api/notes" -Method Post -ContentType "application/json" -Body $body
        $notes = WaitJob $url $job.job_id 1200
        $midi = Join-Path $out "notes.mid"
        $xml = Join-Path $out "notes.musicxml"
        if ($notes.state -eq "done") {
            Invoke-WebRequest "${url}api/notes/$($job.job_id).mid" -OutFile $midi
            Invoke-WebRequest "${url}api/notes/$($job.job_id).musicxml" -OutFile $xml
        }
        $count = @($notes.notes.notes).Count
        "state=$($notes.state) notes=$count instrument=$($notes.notes.instrument) midi_bytes=$((Get-Item $midi -ErrorAction SilentlyContinue).Length) musicxml_bytes=$((Get-Item $xml -ErrorAction SilentlyContinue).Length) error=$($notes.error)" | Set-Content (Join-Path $out "notes.txt")
        Say "notes: $($notes.state), $count notes"
    } catch { Say "notes check failed: $_" } }

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

    try {
    Say "the app's window: start as from the shortcut (no arguments)"
    Remove-Item Env:CHORDCHART_IDLE_EXIT
    Start-Process $exe
    $windows = 0
    foreach ($i in 1..60) { Start-Sleep 1; $windows = WindowCount; if ($windows -ge 1) { break } }
    $port = (Get-Content (Join-Path $env:LOCALAPPDATA "ChordChart\$($variant.instance).port") | ConvertFrom-Json).port
    $windowUrl = "http://127.0.0.1:$port/"
    $windowInfo = Invoke-RestMethod "${windowUrl}api/app"
    Say "second launch (must hand over to the running app and exit)"
    $second = Start-Process $exe -PassThru
    $secondExited = $second.WaitForExit(30000)
    Start-Sleep 3
    $windowsAfter = WindowCount
    Invoke-RestMethod "${windowUrl}api/quit" -Method Post -ContentType "application/json" -Body "{}" | Out-Null
    Start-Sleep 10
    "windows=$windows window_mode=$($windowInfo.window) second_exited=$secondExited windows_after_second=$windowsAfter after_quit=$(AppCount)" | Set-Content (Join-Path $out "window.txt")
    Say "window: $windows, window mode: $($windowInfo.window), second launch exited: $secondExited, after quit: $(AppCount)"
    } catch { Say "window check failed: $_"; Get-Process ChordChart -ErrorAction SilentlyContinue | Stop-Process -Force }

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
} catch {
    Say "ERROR: $_"
} finally {
    if ($appLog) { Copy-Item $appLog (Join-Path $out "app-log.txt") -ErrorAction SilentlyContinue }
    Say "done"
    "done" | Set-Content (Join-Path $out "done.txt")
}
