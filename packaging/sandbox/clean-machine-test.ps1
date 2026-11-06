# Runs inside Windows Sandbox, started by start-clean-machine-test.ps1. Installs the Windows
# build the way docs/INSTALL.md says, runs its checks, opens the desktop app, and writes
# clean-machine-test.log and summary.json to C:\earwitness-results (a folder on the host).

# The parameters let it be tried outside the sandbox against scratch folders.
param(
    [string] $Source = "C:\earwitness-test",
    [string] $Out = "C:\earwitness-results",
    [string] $UserHome = $env:USERPROFILE
)
$ErrorActionPreference = "Continue"
Start-Transcript -Path (Join-Path $out "clean-machine-test.log") -Force | Out-Null
$checks = [ordered]@{}
function Record([string] $name, [bool] $ok, [string] $detail) {
    $checks[$name] = [ordered]@{ ok = $ok; detail = $detail }
    $mark = if ($ok) { "PASS" } else { "FAIL" }
    Write-Host ("{0}  {1}: {2}" -f $mark, $name, $detail)
}

Write-Host "Earwitness clean-machine test, $(Get-Date -Format o)"
$os = Get-CimInstance Win32_OperatingSystem
Write-Host "Windows: $($os.Caption) $($os.Version)"
$pythonFound = Get-Command python.exe, py.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notlike "*WindowsApps*" }
Write-Host "Python installed: $([bool]$pythonFound); ffmpeg on PATH: $([bool](Get-Command ffmpeg -ErrorAction SilentlyContinue))"
$webview = Get-ItemProperty "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" -ErrorAction SilentlyContinue
Write-Host "WebView2 Runtime: $(if ($webview) { $webview.pv } else { 'not installed' })"
Write-Host ""

# Step 1: the download, marked as coming from the internet the way a browser marks it.
$zip = Get-ChildItem -Path $source -Filter "*.zip" | Select-Object -First 1
$downloads = Join-Path $UserHome "Downloads"
New-Item -ItemType Directory -Force -Path $downloads | Out-Null
Copy-Item -Path $zip.FullName, "$($zip.FullName).sha256" -Destination $downloads
Set-Location $downloads
$zipPath = Join-Path $downloads $zip.Name
Set-Content -Path $zipPath -Stream Zone.Identifier -Value "[ZoneTransfer]`r`nZoneId=3"

# Step 2: the checksum.
$hash = (Get-FileHash $zipPath -Algorithm SHA256).Hash
$expected = ((Get-Content "$zipPath.sha256") -split "\s+")[0]
Record "checksum matches" ($hash -ieq $expected) $hash

# Step 3: unblock and extract.
Unblock-File $zipPath
Expand-Archive $zipPath -DestinationPath (Join-Path $UserHome "Earwitness") -Force
$app = Join-Path (Join-Path $UserHome "Earwitness") $zip.BaseName
Record "extracted" (Test-Path (Join-Path $app "earwitness.exe")) $app

# Step 4: the install check.
Set-Location $app
$version = (& .\earwitness.exe --version 2>&1) -join " "
Record "earwitness.exe runs" ($LASTEXITCODE -eq 0) $version
$demo = & .\earwitness.exe demo --workers 2 2>&1
$demoCode = $LASTEXITCODE
$demo | ForEach-Object { Write-Host "    $_" }
$verdicts = ($demo | Select-String -CaseSensitive -Pattern "PASS|FAIL|demo passed|demo FAILED" | ForEach-Object { $_.Line.Trim() }) -join "; "
Record "demo" ($demoCode -eq 0) $verdicts
$audit = & .\earwitness.exe audit (Join-Path $app "earwitness-demo\aircraft\recording.wav") 2>&1
Record "audit" (($LASTEXITCODE -eq 0) -and (($audit -join " ") -match "aircraft\s+likely")) "aircraft clue found in the demo recording"

# Step 5: the desktop app, started the way a double-click starts it, left open. The launcher
# appends to its log, so an earlier run's address must not be read as this one's.
$log = Join-Path $app "earwitness-ui.log"
Remove-Item $log -ErrorAction SilentlyContinue
$ui = Start-Process -FilePath (Join-Path $app "earwitness-ui.exe") -WorkingDirectory $app -PassThru
$url = $null
for ($i = 0; $i -lt 120 -and -not $url; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Path $log) {
        $line = Get-Content $log | Where-Object { $_ -like "Earwitness UI: *" } | Select-Object -First 1
        if ($line) { $url = $line.Substring(15).Trim() }
    }
}
if ($url) {
    $uri = [Uri]$url
    $base = "$($uri.Scheme)://$($uri.Authority)"
    $token = $uri.Query.Substring(7)
    try {
        $page = Invoke-WebRequest -UseBasicParsing "$base/"
        $runs = Invoke-WebRequest -UseBasicParsing "$base/api/runs" -Headers @{ "x-earwitness-token" = $token }
        $listed = ($runs.Content -match "demo-thunder") -and ($runs.Content -match "demo-aircraft")
        $mode = if ((Get-Content $log -Raw) -match "opening the system browser instead") { "browser fallback" } else { "app window" }
        Record "desktop app" (($page.StatusCode -eq 200) -and $listed) "$mode; page $($page.StatusCode); demo runs listed: $listed"
    }
    catch {
        Record "desktop app" $false $_.Exception.Message
    }
}
else {
    Record "desktop app" $false "no server address in earwitness-ui.log"
}
Write-Host ""
Write-Host "earwitness-ui.log:"
Get-Content $log -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" }
Copy-Item $log -Destination $out -ErrorAction SilentlyContinue

$allOk = -not ($checks.Values | Where-Object { -not $_.ok })
[ordered]@{ passed = $allOk; checks = $checks; when = (Get-Date -Format o) } |
    ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $out "summary.json") -Encoding UTF8
Write-Host ""
Write-Host ("Clean-machine test: " + $(if ($allOk) { "PASSED" } else { "FAILED" }))
Write-Host "The app stays open: its Results page should list demo-thunder and demo-aircraft."
Stop-Transcript | Out-Null
