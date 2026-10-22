<#
.SYNOPSIS
  Loop video files into MediaMTX as fake RTSP cameras.

.EXAMPLE
  # Every .mp4 in ./samples becomes cam2, cam3, cam4 ... (cam1 is left for the phone)
  .\scripts\fake-cameras.ps1

.EXAMPLE
  .\scripts\fake-cameras.ps1 -StartIndex 1 -Width 640 -Height 360
#>
param(
    [string]$SamplesDir = "$PSScriptRoot\..\samples",
    [int]$StartIndex = 2,
    [int]$MaxCameras = 3,
    [int]$Width = 640,
    [int]$Height = 480,
    [int]$Fps = 15,
    [string]$Server = "localhost:8554",
    [string]$User = "publisher",
    [string]$Pass = ""
)
# Publish password: MEDIAMTX_PUBLISH_PASSWORD in .env (MediaMTX gets the same value via compose.yaml).
if (-not $Pass) {
    $envFile = Join-Path $PSScriptRoot "..\.env"
    $line = if (Test-Path $envFile) { Get-Content $envFile | Where-Object { $_ -match '^\s*MEDIAMTX_PUBLISH_PASSWORD\s*=' } | Select-Object -First 1 }
    if (-not $line) { Write-Error "Set MEDIAMTX_PUBLISH_PASSWORD in .env (see .env.example)"; exit 1 }
    $Pass = ($line -split "=", 2)[1].Trim()
}


$clips = Get-ChildItem -Path $SamplesDir -Include *.mp4, *.mov, *.mkv -File -Recurse |
    Select-Object -First $MaxCameras
if (-not $clips) {
    Write-Error "No video files in $SamplesDir. Record a clip on your phone and copy it there."
    exit 1
}

$procs = @()
$i = $StartIndex
foreach ($clip in $clips) {
    $url = "rtsp://${User}:${Pass}@${Server}/cam$i"
    # Re-encode to a camera-like substream: small, fixed fps, short GOP, no audio.
    $ffArgs = @(
        "-hide_banner", "-loglevel", "warning",
        # Start-Process does not quote arguments, so paths with spaces must be quoted here.
        "-re", "-stream_loop", "-1", "-i", "`"$($clip.FullName)`"",
        "-an",
        "-vf", "scale=${Width}:${Height}:force_original_aspect_ratio=decrease,pad=${Width}:${Height}:(ow-iw)/2:(oh-ih)/2,fps=$Fps",
        "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
        "-g", ($Fps * 2), "-pix_fmt", "yuv420p",
        "-f", "rtsp", "-rtsp_transport", "tcp", $url
    )
    $procs += Start-Process ffmpeg -ArgumentList $ffArgs -NoNewWindow -PassThru
    Write-Host "cam$i <- $($clip.Name)   (read at rtsp://$Server/cam$i)"
    $i++
}

Write-Host "`nStreaming $($procs.Count) fake camera(s). Press Ctrl+C to stop."
try {
    while ($true) {
        foreach ($p in $procs) {
            if ($p.HasExited) { Write-Warning "ffmpeg (pid $($p.Id)) exited with code $($p.ExitCode)" }
        }
        $procs = @($procs | Where-Object { -not $_.HasExited })
        if (-not $procs) { break }
        Start-Sleep -Seconds 2
    }
} finally {
    $procs | Where-Object { -not $_.HasExited } | Stop-Process -Force
}
