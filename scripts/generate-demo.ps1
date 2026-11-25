# UNTESTED. Generates synthetic CSVs and migrates a demo database outside the repository.
param([double]$Scale = 1.0, [int]$Seed = 42)
$ErrorActionPreference = "Stop"
$dir = if ($env:AZFINOPS_DATA_DIR) { $env:AZFINOPS_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "azfinops" }
$out = Join-Path $dir "synthetic"
New-Item -ItemType Directory -Force -Path $out | Out-Null
uv run azfinops-generate --out $out --seed $Seed --scale $Scale
uv run azfinops-migrate (Join-Path $dir "demo.duckdb")
Write-Host "Synthetic data in $out ; database $dir\demo.duckdb (empty until the Phase 3 importer exists)"
