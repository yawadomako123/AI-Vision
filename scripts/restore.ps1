<#
.SYNOPSIS
  Restore a database backup made by backup.ps1.

.EXAMPLE
  .\scripts\restore.ps1 -Dump backups\aivision-20261016-030000.dump
  .\scripts\restore.ps1 -Dump backups\aivision-20261016-030000.dump -Database aivision_restore_test
  # -Database restores into a separate database, e.g. to verify a backup without touching live data.
#>
param(
    [Parameter(Mandatory)][string]$Dump,
    [string]$Database = ""
)
# Native tools write notices to stderr; PowerShell 5 would treat those as errors. Check exit codes instead.
$ErrorActionPreference = "Continue"
$root = Resolve-Path "$PSScriptRoot\.."
$compose = @("-f", "$root\compose.yaml")
$container = (docker compose @compose ps -q db)
docker cp (Resolve-Path $Dump) "${container}:/tmp/restore.dump" | Out-Null

if ($Database) {
    docker compose @compose exec -T db sh -c "dropdb -U `"`$POSTGRES_USER`" --if-exists $Database && createdb -U `"`$POSTGRES_USER`" $Database" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "could not create database $Database" }
    docker compose @compose exec -T db sh -c "pg_restore -U `"`$POSTGRES_USER`" -d $Database --no-owner /tmp/restore.dump"
} else {
    Write-Warning "Restoring over the live database. Stop the API and worker first."
    docker compose @compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner /tmp/restore.dump'
}
if ($LASTEXITCODE -ne 0) { throw "pg_restore failed" }
docker compose @compose exec -T db rm -f /tmp/restore.dump
Write-Host "Restored $Dump into $(if ($Database) { $Database } else { 'the live database' })"
