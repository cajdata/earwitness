<#
.SYNOPSIS
Runs the release's clean-machine check in Windows Sandbox.

.DESCRIPTION
Windows Sandbox starts a fresh Windows with nothing installed: no Python, no ffmpeg, no
developer tools. This script stages the Windows build zip and its checksum with
clean-machine-test.ps1, writes a .wsb configuration that maps them read-only into the sandbox
and maps a results folder read-write, and starts the sandbox. Inside, the test installs the
build the way docs/INSTALL.md says, runs `earwitness.exe demo`, opens the desktop app, and
writes clean-machine-test.log and summary.json to the results folder. The app stays open for
a look at its Results page; close the sandbox window when done (it discards everything).

Windows Sandbox needs Windows 10 or 11 Pro, Enterprise or Education, enabled once as
administrator, then a restart:
  Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All

.EXAMPLE
powershell -ExecutionPolicy Bypass -File packaging\sandbox\start-clean-machine-test.ps1 -Zip dist\windows\earwitness-0.1.0-windows-x64.zip
#>
param(
    [Parameter(Mandatory = $true)] [string] $Zip,
    [string] $Results = (Join-Path (Get-Location) "sandbox-results")
)
$ErrorActionPreference = "Stop"

if (-not (Test-Path "$env:windir\System32\WindowsSandbox.exe")) {
    throw "Windows Sandbox is not enabled. As administrator run: Enable-WindowsOptionalFeature -Online -FeatureName Containers-DisposableClientVM -All, then restart."
}
$zipPath = (Resolve-Path $Zip).Path
$shaPath = "$zipPath.sha256"
if (-not (Test-Path $shaPath)) { throw "The checksum file $shaPath is missing." }

$stage = Join-Path ([IO.Path]::GetTempPath()) ("earwitness-sandbox-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -Path $zipPath, $shaPath, (Join-Path $PSScriptRoot "clean-machine-test.ps1") -Destination $stage
New-Item -ItemType Directory -Force -Path $Results | Out-Null
$resultsPath = (Resolve-Path $Results).Path

$wsb = Join-Path $stage "earwitness-clean-machine-test.wsb"
@"
<Configuration>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>$stage</HostFolder>
      <SandboxFolder>C:\earwitness-test</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
    <MappedFolder>
      <HostFolder>$resultsPath</HostFolder>
      <SandboxFolder>C:\earwitness-results</SandboxFolder>
      <ReadOnly>false</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <LogonCommand>
    <Command>powershell.exe -NoExit -ExecutionPolicy Bypass -File C:\earwitness-test\clean-machine-test.ps1</Command>
  </LogonCommand>
</Configuration>
"@ | Set-Content -Path $wsb -Encoding UTF8

Write-Host "Starting Windows Sandbox with $(Split-Path $zipPath -Leaf)."
Write-Host "Results will be written to $resultsPath"
Start-Process -FilePath $wsb
