<#
.SYNOPSIS
  Packs the Azure FinOps dashboard into two zips for moving to another PC.

.DESCRIPTION
  azfinops-app.zip   the project folder, without what the new PC rebuilds (.venv, node_modules, dist, caches)
  azfinops-data.zip  the data folder: the database and saved settings, without migration backups
  SHA256SUMS.txt     checksums, checked by unpack_transfer.ps1 on the new PC

  Uses Windows' built-in tar.exe, which handles files over 4 GB (Compress-Archive does not).
  Stop the dashboard server first: the script refuses to pack while it is running.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\pack_transfer.ps1 -Destination E:\azfinops-transfer
  powershell -ExecutionPolicy Bypass -File scripts\pack_transfer.ps1 -Destination E:\azfinops-transfer -NoSamples
#>
param(
  [Parameter(Mandatory = $true)][string]$Destination,
  [string]$Project = "",   # default: the folder above scripts\
  [string]$DataDir = $(if ($env:AZFINOPS_DATA_DIR) { $env:AZFINOPS_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "azfinops" }),
  [switch]$NoSamples       # leave out the export CSVs in samples\ (their data is already in the database)
)
$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 can leave $PSScriptRoot empty inside param(); work it out here instead.
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { (Get-Location).Path }
if (-not $Project) {
  $Project = Split-Path -Parent $ScriptDir
  if (-not (Test-Path (Join-Path $Project "pyproject.toml")) -and (Test-Path "pyproject.toml")) { $Project = (Get-Location).Path }
}

function Fail($msg) { Write-Host "STOPPED: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) { Fail "tar.exe was not found (it ships with Windows 10 1803 and later)." }
if (-not (Test-Path (Join-Path $Project "pyproject.toml"))) { Fail "$Project does not look like the project folder (no pyproject.toml)." }
$db = Join-Path $DataDir "azfinops.duckdb"
if (-not (Test-Path $db)) { Fail "No database at $db. Set -DataDir if it lives elsewhere." }

# The server holds the database open; a copy taken now could be inconsistent.
if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) {
  Fail "The dashboard server is running on port 8765. Stop it (Ctrl+C in its window) and run this again."
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$probe = Join-Path $Destination ".write-test"
try { Set-Content -Path $probe -Value "ok"; Remove-Item $probe } catch { Fail "Cannot write to $Destination ($($_.Exception.Message))." }

$appZip  = Join-Path $Destination "azfinops-app.zip"
$dataZip = Join-Path $Destination "azfinops-data.zip"
foreach ($z in $appZip, $dataZip) { if (Test-Path $z) { Remove-Item $z } }

# Free space: the zips are at most the size of what goes in them.
$need = (Get-Item $db).Length * 1.1
$drive = (Get-Item $Destination).PSDrive
if ($drive -and $drive.Free -and $drive.Free -lt $need) {
  Fail ("{0} has {1:N1} GB free; the database alone is {2:N1} GB." -f $Destination, ($drive.Free / 1GB), ((Get-Item $db).Length / 1GB))
}

$exclude = @(".venv", "node_modules", "dist", ".pytest_tmp", ".pytest_cache", ".ruff_cache", ".mypy_cache", "__pycache__", ".git")
if ($NoSamples) { $exclude += "samples" }

$parent = Split-Path -Parent $Project
$leaf   = Split-Path -Leaf $Project
Write-Host "Packing the project from $Project ..."
# List the files ourselves and never enter an excluded folder: tar would still try to read inside them, and a
# leftover cache folder with broken permissions (.pytest_cache) then stops the whole pack.
$skipped = New-Object System.Collections.Generic.List[string]
function Get-ProjectFiles([string]$dir) {
  try { $entries = Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop } catch { $skipped.Add($dir); return }
  foreach ($e in $entries) {
    if ($e.PSIsContainer) {
      if ($exclude -contains $e.Name) { continue }
      if ($e.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }   # junctions and links
      Get-ProjectFiles $e.FullName
    } else { $e.FullName }
  }
}
$files = @(Get-ProjectFiles $Project)
if ($files.Count -eq 0) { Fail "No files found under $Project." }
$list = Join-Path $env:TEMP "azfinops-pack-list.txt"
# paths relative to the parent folder, so the zip unpacks into a single azfinops\ folder
$rel = $files | ForEach-Object { $_.Substring($parent.TrimEnd('\').Length + 1) }
[IO.File]::WriteAllLines($list, [string[]]$rel, (New-Object Text.UTF8Encoding $false))   # no BOM: tar would read it as part of the first name
& tar.exe -a -c -f $appZip -C $parent -T $list
$code = $LASTEXITCODE
Remove-Item $list -ErrorAction SilentlyContinue
if ($code -ne 0) { Fail "tar failed while packing the project (exit $code)." }
if ($skipped.Count) { Write-Host ("Skipped {0} folder(s) that could not be read: {1}" -f $skipped.Count, ($skipped -join ", ")) -ForegroundColor Yellow }

Write-Host "Packing the data from $DataDir (this is the slow part) ..."
& tar.exe -a -c -f $dataZip "--exclude=*.bak-*" "--exclude=*.wal" -C $DataDir .
if ($LASTEXITCODE -ne 0) { Fail "tar failed while packing the data (exit $LASTEXITCODE)." }

Write-Host "Writing checksums ..."
$sums = foreach ($z in $appZip, $dataZip) { "{0}  {1}" -f (Get-FileHash $z -Algorithm SHA256).Hash, (Split-Path -Leaf $z) }
$sums | Set-Content -Path (Join-Path $Destination "SHA256SUMS.txt") -Encoding ascii
Copy-Item (Join-Path $ScriptDir "unpack_transfer.ps1") $Destination -Force

Write-Host ""
Write-Host "Done. In $Destination :" -ForegroundColor Green
Get-ChildItem $Destination -File | ForEach-Object { "  {0,-22} {1,10:N1} MB" -f $_.Name, ($_.Length / 1MB) }
Write-Host ""
Write-Host "On the new PC:  powershell -ExecutionPolicy Bypass -File <this folder>\unpack_transfer.ps1"
Write-Host "These zips hold company billing data: keep them on an encrypted drive or approved share, and delete them after the move."
