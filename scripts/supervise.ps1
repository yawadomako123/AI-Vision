<#
.SYNOPSIS
  Runs AI Vision Edge unattended: starts the Docker services, the API and the vision worker,
  and restarts any of them that crash or hang.

.DESCRIPTION
  - Waits for Docker Desktop (it can take a minute after logging in), then `docker compose up -d`.
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
    [int]$CheckSeconds = 10,
    [int]$WorkerHangSeconds = 120,
    [int]$WorkerStartupSeconds = 300
)

# A supervisor must survive anything: log errors and carry on.
$ErrorActionPreference = "Continue"
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
if (-not $mutex.WaitOne(0)) {
    Write-Host "The supervisor is already running."
    exit 0
}
if ((Test-Path $SupLog) -and (Get-Item $SupLog).Length -gt 5MB) { Move-Item -Force $SupLog "$SupLog.prev" }

# --- processes -------------------------------------------------------------------------------
$Services = @{
    api    = @{ File = "powershell.exe"; Args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$Root\scripts\run-api.ps1`"");
                Dir = $Root; Proc = $null; Fails = 0; NextStart = [datetime]::MinValue; StartedAt = $null; HealthFails = 0 }
    worker = @{ File = "uv"; Args = @("run", "python", "-m", "app.main");
                Dir = (Join-Path $Root "services\vision"); Proc = $null; Fails = 0; NextStart = [datetime]::MinValue; StartedAt = $null }
}

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
    while ((Get-Date) -lt $deadline) {
        # Through cmd: Windows PowerShell turns a native program's stderr into error records.
        cmd.exe /c "docker info >nul 2>&1"
        if ($LASTEXITCODE -eq 0) {
            $out = cmd.exe /c "cd /d `"$Root`" && docker compose up -d 2>&1"
            $out | ForEach-Object { Log "docker: $_" }
            return $true
        }
        Log "waiting for Docker Desktop..."
        Start-Sleep -Seconds 15
    }
    return $false
}

# --- main loop -------------------------------------------------------------------------------
try {
    Log "supervisor starting in $Root"
    if (-not (Start-Docker)) { Log "Docker didn't start within 10 min; continuing (the API will retry the database)" }
    while ($true) {
      try {
        foreach ($name in @("api", "worker")) {
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
