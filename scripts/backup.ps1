<#
.SYNOPSIS
  Back up the database (and optionally the event snapshots) to .\backups.

.EXAMPLE
  .\scripts\backup.ps1                     # database only
  .\scripts\backup.ps1 -IncludeSnapshots   # plus storage\events as a .zip
#>
param([switch]$IncludeSnapshots)
# Native tools write notices to stderr; PowerShell 5 would treat those as errors. Check exit codes instead.
$ErrorActionPreference = "Continue"
$root = Resolve-Path "$PSScriptRoot\.."
$dir = Join-Path $root "backups"
New-Item -ItemType Directory -Force $dir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

$dump = Join-Path $dir "aivision-$stamp.dump"
# Custom format (-Fc): compressed, and pg_restore can restore selectively. Written inside the
# container and copied out, because PowerShell 5 pipe redirection corrupts binary output.
docker compose -f "$root\compose.yaml" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/backup.dump'
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
$container = (docker compose -f "$root\compose.yaml" ps -q db)
docker cp "${container}:/tmp/backup.dump" $dump | Out-Null
docker compose -f "$root\compose.yaml" exec -T db rm -f /tmp/backup.dump
Write-Host "Database -> $dump ($([math]::Round((Get-Item $dump).Length / 1KB)) KB)"

if ($IncludeSnapshots) {
    $zip = Join-Path $dir "snapshots-$stamp.zip"
    Compress-Archive -Path (Join-Path $root "storage\events\*") -DestinationPath $zip
    Write-Host "Snapshots -> $zip"
}
