<#
.SYNOPSIS
  Download the native Windows MediaMTX (camera hub) into tools\mediamtx, so no Docker is needed.

.DESCRIPTION
  Fetches a pinned release from the official GitHub project (bluenviron/mediamtx, MIT licence),
  checks its SHA-256 against the project's published checksums before unpacking, and keeps only
  mediamtx.exe (our own config is infra\mediamtx\mediamtx.yml). Safe to run again.

.EXAMPLE
  .\scripts\get-mediamtx.ps1
#>
param([string]$Version = "v1.21.2")

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$root = (Resolve-Path "$PSScriptRoot\..").Path
$dest = Join-Path $root "tools\mediamtx"
$exe = Join-Path $dest "mediamtx.exe"
$stamp = Join-Path $dest "VERSION"

if ((Test-Path $exe) -and (Test-Path $stamp) -and ((Get-Content $stamp -Raw).Trim() -eq $Version)) {
    Write-Host "MediaMTX $Version already in $dest"
    exit 0
}

$base = "https://github.com/bluenviron/mediamtx/releases/download/$Version"
$zipName = "mediamtx_${Version}_windows_amd64.zip"
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("mediamtx-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $work | Out-Null
try {
    Write-Host "Downloading $zipName from github.com/bluenviron/mediamtx ..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    # curl.exe (built into Windows 10+) is far faster than Invoke-WebRequest for big files.
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    foreach ($f in @($zipName, "checksums.sha256")) {
        $out = Join-Path $work $f
        if ($curl) { & $curl.Source -fsSL --retry 3 -o $out "$base/$f"; if ($LASTEXITCODE -ne 0) { throw "download of $f failed" } }
        else { Invoke-WebRequest -UseBasicParsing "$base/$f" -OutFile $out }
    }

    $expected = (Get-Content (Join-Path $work "checksums.sha256") | Where-Object { $_ -match [regex]::Escape($zipName) } |
                 Select-Object -First 1) -split "\s+" | Select-Object -First 1
    $actual = (Get-FileHash (Join-Path $work $zipName) -Algorithm SHA256).Hash.ToLower()
    if (-not $expected -or $actual -ne $expected.ToLower()) {
        throw "Checksum mismatch for $zipName (expected $expected, got $actual): not installing it."
    }
    Write-Host "Checksum OK"

    $unzip = Join-Path $work "x"
    Expand-Archive (Join-Path $work $zipName) -DestinationPath $unzip
    New-Item -ItemType Directory -Force $dest | Out-Null
    Copy-Item (Join-Path $unzip "mediamtx.exe") $exe -Force
    if (Test-Path (Join-Path $unzip "LICENSE")) { Copy-Item (Join-Path $unzip "LICENSE") (Join-Path $dest "LICENSE") -Force }
    Set-Content -Path $stamp -Value $Version -Encoding ascii
    Write-Host "Installed MediaMTX $Version -> $exe"
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
