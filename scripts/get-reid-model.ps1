<#
.SYNOPSIS
  Download the person re-identification model into models\reid (about 1 MB, once).

.DESCRIPTION
  Intel Open Model Zoo person-reidentification-retail-0288 (Apache-2.0), FP16 OpenVINO IR.
  It turns a person crop into an "appearance fingerprint", so someone who leaves the picture
  and comes back keeps their number (services\vision\app\identity.py). Checksums are pinned.
#>
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = (Resolve-Path "$PSScriptRoot\..").Path
$Dir = Join-Path $Root "models\reid"
$Name = "person-reidentification-retail-0288"
$Base = "https://storage.openvinotoolkit.org/repositories/open_model_zoo/2023.0/models_bin/1/$Name/FP16"
$Files = [ordered]@{
    "$Name.xml" = "0d5021c772e603dce69d9ef2e326ecf0a43234cc8b4c5bb5e8aa566f090bdb51"
    "$Name.bin" = "08e24c2f1dd976ff8e1f667b93db2950ac8db8da09e163e1ca6463b82d1dbc42"
}

New-Item -ItemType Directory -Force $Dir | Out-Null
foreach ($file in $Files.Keys) {
    $path = Join-Path $Dir $file
    if ((Test-Path $path) -and (Get-FileHash $path -Algorithm SHA256).Hash.ToLower() -eq $Files[$file]) { continue }
    & curl.exe -fsSL --retry 3 -o $path "$Base/$file"
    if ($LASTEXITCODE -ne 0) { throw "download failed: $file" }
    $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
    if ($actual -ne $Files[$file]) { Remove-Item $path; throw "checksum mismatch for $file" }
}
Write-Host "Re-identification model ready in $Dir"
