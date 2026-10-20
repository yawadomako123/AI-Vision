<#
.SYNOPSIS
  Start AI Vision Edge automatically when you log in to Windows (no admin rights needed).

.DESCRIPTION
  Adds a shortcut to your Startup folder that runs scripts\supervise.ps1 in a hidden window.
  The supervisor starts Docker services, the API and the vision worker, and restarts them if
  they crash. Turn on Docker Desktop's "Start when you sign in" setting too.

.EXAMPLE
  .\scripts\install-autostart.ps1           # install
  .\scripts\install-autostart.ps1 -Remove   # uninstall
#>
param([switch]$Remove)

$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "AI Vision Edge.lnk"

if ($Remove) {
    if (Test-Path $link) { Remove-Item $link; Write-Host "Removed $link" } else { Write-Host "Not installed." }
    exit 0
}

$script = (Resolve-Path "$PSScriptRoot\supervise.ps1").Path
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = "powershell.exe"
$sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$sc.WorkingDirectory = (Resolve-Path "$PSScriptRoot\..").Path
$sc.WindowStyle = 7  # minimized
$sc.Description = "AI Vision Edge supervisor"
$sc.Save()
Write-Host "Installed: AI Vision Edge will start when you log in."
Write-Host "Logs: storage\logs\. To start it now without logging out: .\scripts\supervise.ps1"
