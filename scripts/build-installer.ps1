<#
.SYNOPSIS
  Build the Windows installer: dist\AIVisionEdge-Setup-<version>.exe

.DESCRIPTION
  Stages everything the installer ships, then compiles installer\AIVisionEdge.iss with Inno Setup 6:
    - the app as committed in git (git archive: no .env, data, models or local tools)
    - the pre-built dashboard (apps\web\dist), so customers don't need Node.js
    - the detection model (models\rfdetr-nano.onnx), so customers don't need the ~2 GB conversion tools
    - MediaMTX (scripts\get-mediamtx.ps1, checksum-verified)
    - uv (official release, SHA-256 from GitHub) to fetch Python and the libraries at install time
    - WinSW (official release, pinned SHA-256) to run the supervisor as a Windows service
  Needs: git, Node.js (to build the dashboard), Inno Setup 6 (ISCC.exe).

.EXAMPLE
  .\scripts\build-installer.ps1
#>
param([string]$Version = (Get-Date -Format "yyyy.MM.dd"))

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Build = Join-Path $Root "build\installer"
$Stage = Join-Path $Build "stage"
$Cache = Join-Path $Build "downloads"

$UvVersion = "0.10.7"
$UvSha256 = "8881afb877996a1373a12e816395122a8d39a3ac06cd066272acdb49510cf0fe"
$WinSWVersion = "v2.12.0"
$WinSWSha256 = "05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da"  # not published upstream; pinned from the v2.12.0 release asset

function Step($t) { Write-Host ""; Write-Host "== $t" -ForegroundColor Cyan }
function Get-Verified([string]$url, [string]$file, [string]$sha256) {
    if (-not (Test-Path $file)) {
        Write-Host "   downloading $url"
        & curl.exe -fsSL --retry 3 -o $file $url
        if ($LASTEXITCODE -ne 0) { throw "download failed: $url" }
    }
    $actual = (Get-FileHash $file -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $sha256.ToLower()) { Remove-Item $file; throw "checksum mismatch for $file (expected $sha256, got $actual)" }
    Write-Host "   checksum OK: $(Split-Path $file -Leaf)"
}

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found: winget install --id JRSoftware.InnoSetup -e" }

Step "Staging the app (git HEAD)"
if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
New-Item -ItemType Directory -Force $Stage, $Cache | Out-Null
$zip = Join-Path $Build "app.zip"
& git -C $Root archive --format=zip -o $zip HEAD
if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
Expand-Archive $zip -DestinationPath $Stage -Force
Remove-Item $zip

Step "Dashboard (pre-built)"
& cmd.exe /c "cd /d `"$Root\apps\web`" && npm ci --no-audit --no-fund && npm run build" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "dashboard build failed" }
Copy-Item -Recurse (Join-Path $Root "apps\web\dist") (Join-Path $Stage "apps\web\dist")

Step "Detection model"
$model = Join-Path $Root "models\rfdetr-nano.onnx"
if (-not (Test-Path $model)) { throw "missing ${model}: run setup.ps1 once to create it" }
Copy-Item $model (Join-Path $Stage "models\rfdetr-nano.onnx")

# Optional in the installer (a ticked checkbox): search, vehicle colour/type and plates need it, and
# customers can't export it themselves without the ~2 GB conversion tools.
Step "Search model (SigLIP)"
$siglip = Join-Path $Root "models\siglip"
if (-not (Test-Path "$siglip\vision.onnx")) { throw "missing $siglip\vision.onnx: run setup.ps1 -WithSearch once to create it" }
New-Item -ItemType Directory -Force (Join-Path $Stage "models\siglip") | Out-Null
Get-ChildItem $siglip -File | Copy-Item -Destination (Join-Path $Stage "models\siglip")  # not .ov_cache (per-PC)

Step "Re-identification model"
& (Join-Path $PSScriptRoot "get-reid-model.ps1")
New-Item -ItemType Directory -Force (Join-Path $Stage "models\reid") | Out-Null
Get-ChildItem (Join-Path $Root "models\reid") -File | Copy-Item -Destination (Join-Path $Stage "models\reid")

Step "MediaMTX"
& (Join-Path $PSScriptRoot "get-mediamtx.ps1")
New-Item -ItemType Directory -Force (Join-Path $Stage "tools\mediamtx") | Out-Null
Copy-Item (Join-Path $Root "tools\mediamtx\*") (Join-Path $Stage "tools\mediamtx")

Step "uv $UvVersion"
$uvZip = Join-Path $Cache "uv-$UvVersion.zip"
Get-Verified "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" $uvZip $UvSha256
$uvDir = Join-Path $Stage "tools\uv"
Expand-Archive $uvZip -DestinationPath $uvDir -Force

Step "WinSW $WinSWVersion"
$winsw = Join-Path $Cache "WinSW-x64-$WinSWVersion.exe"
Get-Verified "https://github.com/winsw/winsw/releases/download/$WinSWVersion/WinSW-x64.exe" $winsw $WinSWSha256
New-Item -ItemType Directory -Force (Join-Path $Stage "tools\winsw") | Out-Null
Copy-Item $winsw (Join-Path $Stage "tools\winsw\AIVisionEdge.exe")

Step "Compiling the installer"
$tempOut = Join-Path $Build "output"
$dist = Join-Path $Root "dist"
New-Item -ItemType Directory -Force $tempOut, $dist | Out-Null
for ($attempt = 1; $attempt -le 3; $attempt++) {
    & $iscc "/DSourceDir=$Stage" "/DAppVersion=$Version" "/DOutputDir=$tempOut" (Join-Path $Root "installer\AIVisionEdge.iss")
    if ($LASTEXITCODE -eq 0) { break }
    Write-Warning "Inno Setup failed (attempt $attempt of 3), retrying in 3s..."
    Start-Sleep -Seconds 3
}
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
$tempExe = Join-Path $tempOut "AIVisionEdge-Setup-$Version.exe"
$exe = Join-Path $dist "AIVisionEdge-Setup-$Version.exe"
Move-Item -Force $tempExe $exe
Write-Host ""
Write-Host "Built $exe ($([math]::Round((Get-Item $exe).Length / 1MB)) MB)" -ForegroundColor Green
