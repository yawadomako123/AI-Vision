<#
.SYNOPSIS
  Publish a synthetic test pattern (or a looping clip) to MediaMTX as a fake camera.

.EXAMPLE
  .\scripts\test-stream.ps1                      # color bars on cam1
  .\scripts\test-stream.ps1 -Path cam2 -Clip samples\walk.mp4
#>
param(
    [string]$Path = "cam1",
    [string]$Clip = "",
    [string]$Server = "127.0.0.1:8554",
    [string]$User = "publisher",
    [string]$Pass = "changeme-publish"
)

$url = "rtsp://${User}:${Pass}@${Server}/$Path"
$source = if ($Clip) { @("-re", "-stream_loop", "-1", "-i", $Clip) } else { @("-re", "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=15") }
Write-Host "Publishing $(if ($Clip) { $Clip } else { 'test pattern' }) to rtsp://$Server/$Path (Ctrl+C to stop)"
# Restart automatically if the server drops us (like a camera reconnecting).
while ($true) {
    & ffmpeg -hide_banner -loglevel error @source -an -vf "scale=640:-2,fps=15" -c:v libx264 -preset veryfast -tune zerolatency -g 30 -pix_fmt yuv420p -f rtsp -rtsp_transport tcp $url
    Write-Host "ffmpeg exited ($LASTEXITCODE); restarting in 2 s"
    Start-Sleep -Seconds 2
}
