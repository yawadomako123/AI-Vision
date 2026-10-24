<#
.SYNOPSIS
  Install (or remove) AI Vision Edge as a Windows service. Run as administrator; the Windows
  installer runs this for you.

.DESCRIPTION
  Install:
    1. Python and all libraries go inside the install folder (tools\python, tools\uv-cache), so the
       service account can use them (no per-user Python).
    2. setup.ps1 -Bundled -Yes -NoStart: secrets in .env, SQLite, native MediaMTX, environments,
       database. Nothing is overwritten on an upgrade.
    3. Registers the "AIVisionEdge" service (WinSW, tools\winsw\AIVisionEdge.exe): starts at boot,
       even before anyone logs in, and is restarted by Windows if it stops. It runs
       scripts\supervise.ps1, which keeps the camera hub, the API and the worker running.
    4. Allows cameras/phones to reach the camera hub (RTSP, TCP 8554) on private networks only.
    5. Removes the per-user start-at-login shortcut if there is one (the service replaces it).
  Uninstall (-Remove): stops and removes the service and the firewall rule. Data, recordings,
  backups and .env are kept.

.EXAMPLE
  .\scripts\install-service.ps1
  .\scripts\install-service.ps1 -Remove
#>
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$ServiceName = "AIVisionEdge"
$WinSW = Join-Path $Root "tools\winsw\$ServiceName.exe"
$RuleName = "AI Vision Edge - cameras (RTSP 8554)"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host "Run this as administrator."; exit 1 }
New-Item -ItemType Directory -Force (Join-Path $Root "storage\logs") | Out-Null
Start-Transcript -Path (Join-Path $Root "storage\logs\install.log") -Append | Out-Null

function Remove-ServiceIfPresent {
    if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
        Write-Host "Stopping and removing the $ServiceName service..."
        if (Test-Path $WinSW) { & $WinSW stop | Out-Null; & $WinSW uninstall | Out-Null }
        else { sc.exe stop $ServiceName | Out-Null; sc.exe delete $ServiceName | Out-Null }
    }
}

if ($Remove) {
    Remove-ServiceIfPresent
    netsh advfirewall firewall delete rule name="$RuleName" | Out-Null
    Write-Host "Removed. Your data in $Root\storage, backups and .env are kept."
    exit 0
}

# 1. Python inside the install folder, usable by the service account.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Root "tools\python"
$env:UV_CACHE_DIR = Join-Path $Root "tools\uv-cache"
$env:UV_PYTHON_PREFERENCE = "only-managed"
$uvDir = Join-Path $Root "tools\uv"
$env:PATH = "$uvDir;$env:PATH"

# 2. Everything else (idempotent: keeps existing .env secrets and data).
Remove-ServiceIfPresent  # an upgrade replaces the files the old service was running
$global:LASTEXITCODE = 0
& (Join-Path $PSScriptRoot "setup.ps1") -Bundled -Yes -NoStart
if ($LASTEXITCODE -ne 0) { throw "setup failed (see above)" }

# 3. The service.
$xml = @"
<service>
  <id>$ServiceName</id>
  <name>AI Vision Edge</name>
  <description>Local AI security cameras: camera hub, API + dashboard, and the vision worker.</description>
  <executable>powershell.exe</executable>
  <arguments>-NoProfile -ExecutionPolicy Bypass -File "$Root\scripts\supervise.ps1"</arguments>
  <workingdirectory>$Root</workingdirectory>
  <env name="PATH" value="$uvDir;%PATH%" />
  <env name="UV_PYTHON_INSTALL_DIR" value="$($env:UV_PYTHON_INSTALL_DIR)" />
  <env name="UV_CACHE_DIR" value="$($env:UV_CACHE_DIR)" />
  <env name="UV_PYTHON_PREFERENCE" value="only-managed" />
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <onfailure action="restart" delay="10 sec" />
  <onfailure action="restart" delay="30 sec" />
  <onfailure action="restart" delay="60 sec" />
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>20 sec</stoptimeout>
  <logpath>$Root\storage\logs</logpath>
  <log mode="roll-by-size"><sizeThreshold>5120</sizeThreshold><keepFiles>3</keepFiles></log>
</service>
"@
Set-Content -Path ([IO.Path]::ChangeExtension($WinSW, ".xml")) -Value $xml -Encoding utf8
& $WinSW install
if ($LASTEXITCODE -ne 0) { throw "service registration failed" }

# 4. Cameras and phones on the local network publish to RTSP 8554 (private networks only).
netsh advfirewall firewall delete rule name="$RuleName" | Out-Null
netsh advfirewall firewall add rule name="$RuleName" dir=in action=allow protocol=TCP localport=8554 profile=private | Out-Null

# 5. The service replaces the per-user start-at-login shortcut.
Get-ChildItem "C:\Users\*\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\AI Vision Edge.lnk" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item $_.FullName -Force; Write-Host "Removed old start-at-login shortcut: $($_.FullName)" }

& $WinSW start
Write-Host ""
Write-Host "AI Vision Edge is installed as a Windows service and starting (give it a minute)."
Write-Host "Open http://127.0.0.1:8000 and create your admin account."
