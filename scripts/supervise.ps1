<#
.SYNOPSIS
  Runs AI Vision Edge unattended: starts the camera hub (MediaMTX) and database, the API and the
  vision worker, and restarts any of them that crash or hang. Native mode (MEDIAMTX_MODE=native
  and a SQLite DATABASE_URL in .env) needs no Docker at all.

.DESCRIPTION
  - Starts Docker Desktop if it isn't running and waits for it (a minute or two after a reboot),
    then `docker compose up -d`; checks again every minute and recovers if Docker or a container stops.
  - Starts the API (with database migrations) and the vision worker as hidden background processes.
  - Every 10 s: restarts a process that exited; restarts the API if /health stops answering;
    restarts the worker if its heartbeat file (storage/worker.heartbeat) goes stale (a hang).
    Repeated crashes back off (10 s up to 5 min) so a broken setup doesn't spin the CPU.
  - Logs: storage/logs/supervisor.log, api.log, worker.log (the previous run is kept as *.prev.log).
  - Only one copy runs at a time. Ctrl+C (or closing the window) stops everything it started.

  Start automatically at login: .\scripts\install-autostart.ps1

.EXAMPLE
  .\scripts\supervise.ps1
#>
param(
    [int]$BackupHour = 2,           # nightly database backup after this hour (local time)
    [int]$KeepBackups = 14,
    [int]$CheckSeconds = 10,
    [int]$WorkerHangSeconds = 120,
    [int]$WorkerStartupSeconds = 300
)

# A supervisor must survive anything: log errors and carry on.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"  # no "Reading web response" bars from the health checks
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Logs = Join-Path $Root "storage\logs"
New-Item -ItemType Directory -Force $Logs | Out-Null
$Heartbeat = Join-Path $Root "storage\worker.heartbeat"
$SupLog = Join-Path $Logs "supervisor.log"

function Log([string]$msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $msg
    Write-Host $line
    Add-Content -Path $SupLog -Value $line -Encoding utf8
}

# --- single instance -----------------------------------------------------------------------
$mutex = New-Object System.Threading.Mutex($false, "Global\AIVisionEdgeSupervisor")
try { $owned = $mutex.WaitOne(0) }
catch [System.Threading.AbandonedMutexException] { $owned = $true }  # last run's window was closed: we own it now
if (-not $owned) {
    Write-Host "The supervisor is already running."
    exit 0
}
if ((Test-Path $SupLog) -and (Get-Item $SupLog).Length -gt 5MB) { Move-Item -Force $SupLog "$SupLog.prev" }

# --- mode: native (no Docker) or Docker ---------------------------------------------------------
# Native: SQLite database + MediaMTX as tools\mediamtx\mediamtx.exe (scripts\get-mediamtx.ps1),
# run and restarted by this supervisor like the API and worker. Docker is then never touched.
function Get-EnvValue([string]$key) {
    $line = Get-Content (Join-Path $Root ".env") -ErrorAction SilentlyContinue |
        Where-Object { $_ -match "^\s*$key\s*=" } | Select-Object -First 1
    if ($line) { return ($line -split "=", 2)[1].Trim() } else { return "" }
}
$MediaExe = Join-Path $Root "tools\mediamtx\mediamtx.exe"
$NativeMedia = (Get-EnvValue "MEDIAMTX_MODE") -eq "native"
$SqliteDb = (Get-EnvValue "DATABASE_URL") -like "sqlite*"
$NeedDocker = -not ($NativeMedia -and $SqliteDb)
if ($NativeMedia) {
    if (-not (Test-Path $MediaExe)) { Write-Host "MEDIAMTX_MODE=native but $MediaExe is missing: run scripts\get-mediamtx.ps1"; exit 1 }
    # Same settings Docker passes in compose.yaml; inherited by mediamtx.exe.
    $env:MTX_AUTHINTERNALUSERS_1_PASS = Get-EnvValue "MEDIAMTX_PUBLISH_PASSWORD"
    $env:MTX_AUTHINTERNALUSERS_2_PASS = Get-EnvValue "MEDIAMTX_API_PASSWORD"
    $rec = (Join-Path $Root "storage\recordings").Replace("\", "/")
    $env:MTX_PATHDEFAULTS_RECORDPATH = "$rec/%path/%Y-%m-%d_%H-%M-%S-%f"
    # Like compose.yaml: only RTSP (cameras and phones publish) is open to the network; the control
    # API, playback server and browser video listen on this PC only (remote viewing goes via Tailscale).
    $env:MTX_APIADDRESS = "127.0.0.1:9997"
    $env:MTX_PLAYBACKADDRESS = "127.0.0.1:9996"
    $env:MTX_HLSADDRESS = "127.0.0.1:8888"
    $env:MTX_WEBRTCADDRESS = "127.0.0.1:8889"
}

# --- processes -------------------------------------------------------------------------------
$Services = [ordered]@{}
if ($NativeMedia) {
    $Services.mediamtx = @{ File = $MediaExe; Args = @("`"$Root\infra\mediamtx\mediamtx.yml`"");
                            Dir = $Root; Proc = $null; Fails = 0; NextStart = [datetime]::MinValue; StartedAt = $null }
}
$Services.api = @{ File = "powershell.exe"; Args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$Root\scripts\run-api.ps1`"");
                Dir = $Root; Proc = $null; Fails = 0; NextStart = [datetime]::MinValue; StartedAt = $null; HealthFails = 0 }
$Services.worker = @{ File = "uv"; Args = @("run", "python", "-m", "app.main");
                Dir = (Join-Path $Root "services\vision"); Proc = $null; Fails = 0; NextStart = [datetime]::MinValue; StartedAt = $null }

function Stop-Tree($proc) {
    if ($proc -and -not $proc.HasExited) {
        & taskkill.exe /PID $proc.Id /T /F 2>$null | Out-Null
    }
}

function Start-Service-Proc([string]$name) {
    $s = $Services[$name]
    $out = Join-Path $Logs "$name.log"
    $err = Join-Path $Logs "$name.err.log"
    foreach ($f in @($out, $err)) {
        if (Test-Path $f) { Move-Item -Force $f ($f -replace '\.log$', '.prev.log') }
    }
    $s.Proc = Start-Process -FilePath $s.File -ArgumentList $s.Args -WorkingDirectory $s.Dir -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
    $null = $s.Proc.Handle  # keep a handle open, or Windows PowerShell can't read the exit code later
    $s.StartedAt = Get-Date
    $s.HealthFails = 0
    Log "$name started (pid $($s.Proc.Id))"
}

function Restart-Later([string]$name, [string]$why) {
    $s = $Services[$name]
    Stop-Tree $s.Proc
    $s.Proc = $null
    $s.Fails += 1
    $delay = [Math]::Min(300, 10 * [Math]::Pow(2, [Math]::Min($s.Fails - 1, 5)))
    $s.NextStart = (Get-Date).AddSeconds($delay)
    Log "$name $why; restarting in $delay s (failure $($s.Fails))"
}

function Test-Api {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 10
        return $r.StatusCode -eq 200
    } catch { return $false }
}

# --- Docker ----------------------------------------------------------------------------------
function Start-Docker {
    $deadline = (Get-Date).AddMinutes(10)
    $launched = $false
    while ((Get-Date) -lt $deadline) {
        # Through cmd: Windows PowerShell turns a native program's stderr into error records.
        cmd.exe /c "docker info >nul 2>&1"
        if ($LASTEXITCODE -eq 0) {
            $out = cmd.exe /c "cd /d `"$Root`" && docker compose up -d 2>&1"
            $out | ForEach-Object { Log "docker: $_" }
            return $true
        }
        # Not running (e.g. just after a reboot): start Docker Desktop ourselves, once, so recovery
        # doesn't depend on its "start when you sign in" setting.
        $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (-not $launched -and -not (Get-Process "Docker Desktop" -ErrorAction SilentlyContinue) -and (Test-Path $desktop)) {
            Log "starting Docker Desktop"
            Start-Process $desktop
            $launched = $true
        }
        Log "waiting for Docker Desktop..."
        Start-Sleep -Seconds 15
    }
    return $false
}

# --- nightly backup --------------------------------------------------------------------------
$BackupStamp = Join-Path $Logs "last-backup.txt"
$NextBackupTry = Get-Date
$NextDockerCheck = (Get-Date).AddMinutes(1)
function Invoke-NightlyBackup {
    $today = (Get-Date).ToString("yyyy-MM-dd")
    if ((Get-Date).Hour -lt $BackupHour) { return }
    if ((Test-Path $BackupStamp) -and ((Get-Content $BackupStamp -Raw).Trim() -eq $today)) { return }
    Log "nightly backup starting"
    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Root\scripts\backup.ps1" -Keep $KeepBackups 2>&1
    $code = $LASTEXITCODE
    $out | ForEach-Object { Log "backup: $_" }
    if ($code -eq 0) { Set-Content -Path $BackupStamp -Value $today -Encoding ascii }
    else {
        Log "nightly backup FAILED (exit $code); retrying in an hour"
        $script:NextBackupTry = (Get-Date).AddHours(1)
    }
}

# --- main loop -------------------------------------------------------------------------------
try {
    Log "supervisor starting in $Root"
    if ($NeedDocker) {
        if (-not (Start-Docker)) { Log "Docker didn't start within 10 min; continuing (the API will retry the database)" }
    } else {
        Log "native mode: SQLite + mediamtx.exe, no Docker"
    }
    while ($true) {
      try {
        foreach ($name in @($Services.Keys)) {
            $s = $Services[$name]
            if ($null -eq $s.Proc) {
                if ((Get-Date) -ge $s.NextStart) { Start-Service-Proc $name }
                continue
            }
            if ($s.Proc.HasExited) {
                Restart-Later $name "exited with code $($s.Proc.ExitCode)"
                continue
            }
            $age = ((Get-Date) - $s.StartedAt).TotalSeconds
            if ($name -eq "api" -and $age -gt 60) {
                if (Test-Api) { $s.HealthFails = 0; $s.Fails = 0 }
                else {
                    $s.HealthFails += 1
                    if ($s.HealthFails -ge 3) { Restart-Later $name "stopped answering /health" }
                }
            }
            if ($name -eq "worker" -and $age -gt $WorkerStartupSeconds) {
                $stale = -not (Test-Path $Heartbeat) -or `
                    ((Get-Date) - (Get-Item $Heartbeat).LastWriteTime).TotalSeconds -gt $WorkerHangSeconds
                if ($stale) { Restart-Later $name "heartbeat is stale (hung)" }
                else { $s.Fails = 0 }
            }
        }
        if ((Get-Date) -ge $NextBackupTry) { Invoke-NightlyBackup }
        # Docker Desktop can quit later (updates, a crash, someone closing it): the database and
        # MediaMTX go with it. Check every minute and bring them back.
        if ($NeedDocker -and (Get-Date) -ge $NextDockerCheck) {
            $NextDockerCheck = (Get-Date).AddMinutes(1)
            cmd.exe /c "docker info >nul 2>&1"
            $dockerUp = $LASTEXITCODE -eq 0
            if ($dockerUp) {
                cmd.exe /c "cd /d `"$Root`" && docker compose ps --status running -q db | findstr . >nul 2>&1"
                $dbUp = $LASTEXITCODE -eq 0
                cmd.exe /c "cd /d `"$Root`" && docker compose ps --status running -q mediamtx | findstr . >nul 2>&1"
                $mtxUp = $LASTEXITCODE -eq 0
            }
            if (-not $dockerUp -or -not $dbUp -or -not $mtxUp) {
                Log "Docker services not running (docker=$dockerUp db=$dbUp mediamtx=$mtxUp); recovering"
                if (Start-Docker) { Log "Docker services recovered" } else { Log "Docker still not available; will keep trying" }
            }
        }
      } catch {
        Log "supervisor error (continuing): $_"
      }
        Start-Sleep -Seconds $CheckSeconds
    }
} finally {
    Log "supervisor stopping"
    foreach ($s in $Services.Values) { Stop-Tree $s.Proc }
    $mutex.ReleaseMutex()
}
