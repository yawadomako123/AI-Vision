<#
.SYNOPSIS
  One-command setup for AI Vision Edge on a Windows PC. Safe to run again at any time.

.DESCRIPTION
  1. Checks the free tools are installed: Git, uv, Node.js, FFmpeg (prints the
     winget commands for anything missing).
  2. Creates .env from .env.example and generates every secret. On an existing install it only
     fills in settings that are missing or still placeholders: it never replaces your secrets.
  3. New installs need no Docker: the database is a SQLite file and the camera hub (MediaMTX) is a
     native program downloaded and checksum-verified into tools\mediamtx. Older PostgreSQL/Docker
     installs keep using Docker Desktop.
  4. Installs the Python environments (uv) and the dashboard (npm), and builds the dashboard.
  5. Downloads and converts the detection model if it isn't there yet (~110 MB, once).
  6. Prepares the database.
  7. Optionally: a cloud folder for nightly backup copies, start-at-login, and starting it now.
  Then open http://127.0.0.1:8000 and create your admin account.

.EXAMPLE
  .\scripts\setup.ps1                       # set up (asks a couple of yes/no questions)
  .\scripts\setup.ps1 -Yes                  # same, answering yes to everything
  .\scripts\setup.ps1 -WithSearch           # also install the plain-English search model (~650 MB)
  .\scripts\setup.ps1 -SkipServices         # files and environments only (no Docker/database)
  .\scripts\setup.ps1 -NoStart              # everything except start-at-login / starting it
#>
param(
    [switch]$Yes,
    [switch]$WithSearch,
    [switch]$SkipServices,
    [switch]$NoStart,           # don't set up start-at-login or start it (e.g. the installer runs it as a service)
    [string]$BackupCopyDir = ""
)

$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $Root

function Step([string]$text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ok([string]$text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Info([string]$text) { Write-Host "       $text" }
function Fail([string]$text) { Write-Host "   !!  $text" -ForegroundColor Red; exit 1 }
function Ask([string]$question) {
    if ($Yes) { return $true }
    $a = Read-Host "   ?   $question [Y/n]"
    return ($a -eq "" -or $a -match "^[Yy]")
}
function Run([string]$what, [string]$dir, [string]$command) {
    # Through cmd: Windows PowerShell turns a native program's stderr into errors.
    Info $what
    $out = cmd.exe /c "cd /d `"$dir`" && $command 2>&1"
    if ($LASTEXITCODE -ne 0) {
        $out | Select-Object -Last 15 | ForEach-Object { Write-Host "       $_" }
        Fail "$what failed (see above)"
    }
}

# --- 0. folder location --------------------------------------------------------------------------
# Python packages nest deeply (up to ~130 characters below the project folder). Without Windows
# long paths, a deep project folder breaks installs with misleading "file not found" errors.
$longPaths = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
if ($Root.Length -gt 100 -and $longPaths -ne 1) {
    Write-Host ""
    Write-Host "   !!  This folder's path is too long for Windows ($($Root.Length) characters):" -ForegroundColor Red
    Write-Host "       $Root"
    Write-Host "       Move or clone the project somewhere short, e.g. C:\AI-Vision, and run setup there."
    Write-Host "       (Or enable Windows long paths as administrator:"
    Write-Host "        New-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force)"
    exit 1
}

# --- 1. tools ---------------------------------------------------------------------------------
Step "Checking the free tools"
$tools = [ordered]@{
    git     = "winget install --id Git.Git -e"
    uv      = "winget install --id astral-sh.uv -e"
    node    = "winget install --id OpenJS.NodeJS.LTS -e"
    ffmpeg  = "winget install --id Gyan.FFmpeg -e"
}
$missing = @()
foreach ($t in $tools.Keys) {
    if (Get-Command $t -ErrorAction SilentlyContinue) { Ok $t } else { $missing += $t }
}
if ($missing.Count) {
    Write-Host ""
    Write-Host "   Install these, then open a NEW PowerShell window and run setup again:" -ForegroundColor Yellow
    foreach ($t in $missing) { Write-Host "     $($tools[$t])" }
    exit 1
}

# --- 2. .env ------------------------------------------------------------------------------------
Step "Settings and secrets (.env)"
function New-Secret([int]$bytes, [switch]$Fernet, [switch]$Simple) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    $s = [Convert]::ToBase64String($b).Replace("+", "-").Replace("/", "_")
    if ($Fernet) { return $s }                       # Fernet keys keep their "=" padding
    $s = $s.TrimEnd("=")
    if ($Simple) { $s = $s.Replace("-", "x").Replace("_", "y") }  # easy to type into a phone app
    return $s
}
$envPath = Join-Path $Root ".env"
$newInstall = -not (Test-Path $envPath)
if ($newInstall) {
    Copy-Item (Join-Path $Root ".env.example") $envPath
    Info "created .env from .env.example"
}
$lines = [System.Collections.Generic.List[string]](Get-Content $envPath)
function Get-EnvValue([string]$key) {
    $l = $lines | Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -First 1
    if ($l) { return ($l -split "=", 2)[1].Trim() } else { return $null }
}
function Set-EnvValue([string]$key, [string]$value) {
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^\s*$key\s*=") { $lines[$i] = "$key=$value"; return }
    }
    $lines.Add("$key=$value")
}
$secrets = [ordered]@{
    POSTGRES_PASSWORD          = { New-Secret 24 }
    CAMERA_SECRET_KEY          = { New-Secret 32 -Fernet }
    SERVICE_TOKEN              = { New-Secret 32 }
    MEDIAMTX_PUBLISH_PASSWORD  = { New-Secret 12 -Simple }
    MEDIAMTX_API_PASSWORD      = { New-Secret 24 -Simple }
}
foreach ($k in $secrets.Keys) {
    $v = Get-EnvValue $k
    if (-not $v -or $v -like "replace_with*") {
        Set-EnvValue $k (& $secrets[$k])
        Ok "$k generated"
    } else {
        Ok "$k kept (already set)"
    }
}
# New installs keep their data in one SQLite file (no database server). Existing installs keep
# whatever they use: switching silently would make their data look missing
# (scripts/migrate-to-sqlite.py moves a PostgreSQL install over).
if ($newInstall -and -not (Get-EnvValue "DATABASE_URL")) {
    Set-EnvValue "DATABASE_URL" "sqlite:///storage/aivision.db"
    Ok "database: SQLite file storage\aivision.db"
}
if ($newInstall -and -not (Get-EnvValue "MEDIAMTX_MODE")) {
    Set-EnvValue "MEDIAMTX_MODE" "native"
    Ok "camera hub: native MediaMTX (no Docker)"
}
$native = (Get-EnvValue "MEDIAMTX_MODE") -eq "native" -and (Get-EnvValue "DATABASE_URL") -like "sqlite*"
foreach ($pair in @(@("POSTGRES_DB", "aivision"), @("POSTGRES_USER", "aivision"), @("API_BASE_URL", "http://127.0.0.1:8000"))) {
    if (-not (Get-EnvValue $pair[0])) { Set-EnvValue $pair[0] $pair[1] }
}

# Nightly backups also copied to a cloud-synced folder (survives a dead disk).
$copyDir = if ($BackupCopyDir) { $BackupCopyDir } else { Get-EnvValue "BACKUP_COPY_DIR" }
if (-not $copyDir) {
    $candidates = @()
    if ($env:OneDrive) { $candidates += (Join-Path $env:OneDrive "AI Vision backups") }
    if (Test-Path "G:\My Drive") { $candidates += "G:\My Drive\AI Vision backups" }
    foreach ($c in $candidates) {
        if (Ask "Also copy nightly backups to $c ?") { $copyDir = $c; break }
    }
}
if ($copyDir) {
    Set-EnvValue "BACKUP_COPY_DIR" $copyDir
    Ok "backups will also be copied to $copyDir"
}
# Plain UTF-8 without a BOM: Windows PowerShell's -Encoding utf8 adds one, which some tools misread.
[System.IO.File]::WriteAllLines($envPath, $lines, (New-Object System.Text.UTF8Encoding $false))

# --- 3. camera hub and database ----------------------------------------------------------------------
if (-not $SkipServices -and $native) {
    Step "Camera hub (native MediaMTX, no Docker)"
    & (Join-Path $PSScriptRoot "get-mediamtx.ps1")
    if ($LASTEXITCODE -ne 0 -and -not (Test-Path (Join-Path $Root "tools\mediamtx\mediamtx.exe"))) { Fail "couldn't download MediaMTX" }
    Ok "MediaMTX ready (the supervisor runs it)"
}
if (-not $SkipServices -and -not $native) {
    Step "Database and camera hub (Docker)"
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Fail "This install uses Docker (PostgreSQL or MEDIAMTX_MODE=docker): winget install --id Docker.DockerDesktop -e"
    }
    cmd.exe /c "docker info >nul 2>&1"
    if ($LASTEXITCODE -ne 0) {
        $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (Test-Path $desktop) { Info "starting Docker Desktop..."; Start-Process $desktop }
        $deadline = (Get-Date).AddMinutes(5)
        do { Start-Sleep 5; cmd.exe /c "docker info >nul 2>&1" } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
        if ($LASTEXITCODE -ne 0) { Fail "Docker Desktop didn't start. Open it once by hand, then run setup again." }
    }
    Run "docker compose up -d" $Root "docker compose up -d"
    Ok "PostgreSQL and MediaMTX running"
}

# --- 4. environments and dashboard --------------------------------------------------------------
Step "Installing (this takes a few minutes the first time)"
Run "API environment (uv sync)" (Join-Path $Root "services\backend") "uv sync"
Run "Vision worker environment (uv sync)" (Join-Path $Root "services\vision") "uv sync"
Run "Dashboard packages (npm ci)" (Join-Path $Root "apps\web") "npm ci --no-audit --no-fund"
Run "Building the dashboard" (Join-Path $Root "apps\web") "npm run build"
Ok "installed"

# --- 5. models ----------------------------------------------------------------------------------
Step "AI models"
$model = Join-Path $Root "models\rfdetr-nano.onnx"
if (Test-Path $model) { Ok "detection model present" }
else { Run "Downloading and converting the detection model (~110 MB, once)" (Join-Path $Root "services\vision") "uv run --group export python ..\..\scripts\export_model.py nano"; Ok "detection model ready" }
$siglip = Join-Path $Root "models\siglip\vision.onnx"
if (Test-Path $siglip) { Ok "search model present" }
elseif ($WithSearch) { Run "Downloading the search model (~650 MB, once)" (Join-Path $Root "services\vision") "uv run --group export python ..\..\scripts\export_siglip.py"; Ok "search model ready" }
else { Info "search model skipped (optional: run setup with -WithSearch)" }

# --- 6. database ----------------------------------------------------------------------------------
if (-not $SkipServices) {
    Step "Database"
    if (-not ((Get-EnvValue "DATABASE_URL") -like "sqlite*")) {
        $deadline = (Get-Date).AddMinutes(2)
        do { Start-Sleep 2; cmd.exe /c "cd /d `"$Root`" && docker compose exec -T db pg_isready >nul 2>&1" } while ($LASTEXITCODE -ne 0 -and (Get-Date) -lt $deadline)
    }
    Run "Preparing tables (migrations)" (Join-Path $Root "services\backend") "uv run alembic upgrade head"
    Ok "database ready"
}

# --- 7. run it ----------------------------------------------------------------------------------
if (-not $SkipServices -and -not $NoStart) {
    Step "Running it"
    if (Ask "Start AI Vision Edge automatically when you log in to Windows?") {
        & (Join-Path $PSScriptRoot "install-autostart.ps1") | ForEach-Object { Info $_ }
    }
    $running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -like '*-File*supervise.ps1*' }
    if ($running) { Ok "already running" }
    elseif (Ask "Start it now?") {
        $cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\scripts\supervise.ps1`""
        $null = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cmd; CurrentDirectory = $Root }
        Ok "started in the background (logs: storage\logs)"
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  1. Open http://127.0.0.1:8000 (give it a minute) and create your admin account."
Write-Host "  2. Phone as a camera (Larix): rtsp://<this PC's IP>:8554/cam1, user publisher,"
Write-Host "     password = MEDIAMTX_PUBLISH_PASSWORD in .env"
if ((Get-Command tailscale -ErrorAction SilentlyContinue) -or (Test-Path (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"))) {
    Write-Host "  3. Remote access: tailscale serve --bg 8000   (then open the https://... address on your phone)"
} else {
    Write-Host "  3. Remote access: install Tailscale (winget install --id Tailscale.Tailscale -e), then: tailscale serve --bg 8000"
}
Write-Host "  Keep a copy of .env somewhere safe: it holds the key that unlocks camera passwords in backups."
