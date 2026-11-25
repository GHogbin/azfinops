<#
.SYNOPSIS
  Unpacks the zips made by pack_transfer.ps1 on the new PC, then rebuilds and checks the dashboard.

.DESCRIPTION
  1. Checks the zips against SHA256SUMS.txt.
  2. Unpacks azfinops-app.zip into the parent of -Project and azfinops-data.zip into -DataDir.
     Refuses to overwrite an existing database unless -Force is given.
  3. Unless -NoRebuild: uv sync, npm ci, npm run build, the migration check and the tests.
     Needs uv and Node.js installed first:  winget install astral-sh.uv ; winget install OpenJS.NodeJS.LTS

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File E:\azfinops-transfer\unpack_transfer.ps1
#>
param(
  [string]$Source = "",    # default: the folder this script is in
  [string]$Project = (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "azfinops"),
  [string]$DataDir = $(if ($env:AZFINOPS_DATA_DIR) { $env:AZFINOPS_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "azfinops" }),
  [switch]$Force,          # overwrite an existing database in -DataDir
  [switch]$NoRebuild       # only unpack
)
$ErrorActionPreference = "Stop"

# Windows PowerShell 5.1 can leave $PSScriptRoot empty inside param(); work it out here instead.
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { (Get-Location).Path }
if (-not $Source) { $Source = $ScriptDir }
function Fail($msg) { Write-Host "STOPPED: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) { Fail "tar.exe was not found (it ships with Windows 10 1803 and later)." }
$appZip  = Join-Path $Source "azfinops-app.zip"
$dataZip = Join-Path $Source "azfinops-data.zip"
foreach ($z in $appZip, $dataZip) { if (-not (Test-Path $z)) { Fail "$z is missing." } }

$sumFile = Join-Path $Source "SHA256SUMS.txt"
if (Test-Path $sumFile) {
  Write-Host "Checking the zips ..."
  foreach ($line in Get-Content $sumFile) {
    $hash, $name = $line -split "\s+", 2
    $actual = (Get-FileHash (Join-Path $Source $name) -Algorithm SHA256).Hash
    if ($actual -ne $hash) { Fail "$name is damaged or incomplete (checksum differs). Copy it again." }
  }
} else { Write-Host "No SHA256SUMS.txt; skipping the checksum check." -ForegroundColor Yellow }

$db = Join-Path $DataDir "azfinops.duckdb"
if ((Test-Path $db) -and -not $Force) { Fail "A database already exists at $db. Move it aside, or run again with -Force to overwrite it." }
if ((Test-Path (Join-Path $Project "pyproject.toml")) -and -not $Force) { Fail "The project already exists at $Project. Move it aside, or run again with -Force." }

$parent = Split-Path -Parent $Project
New-Item -ItemType Directory -Force -Path $parent, $DataDir | Out-Null
Write-Host "Unpacking the project into $parent ..."
& tar.exe -x -f $appZip -C $parent
if ($LASTEXITCODE -ne 0) { Fail "tar failed while unpacking the project (exit $LASTEXITCODE)." }
Write-Host "Unpacking the data into $DataDir ..."
& tar.exe -x -f $dataZip -C $DataDir
if ($LASTEXITCODE -ne 0) { Fail "tar failed while unpacking the data (exit $LASTEXITCODE)." }

if ($NoRebuild) { Write-Host "Unpacked. Rebuild with: uv sync; cd web; npm ci; npm run build" -ForegroundColor Green; exit 0 }

foreach ($tool in "uv", "npm") {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
    Fail "$tool is not installed or not on PATH. Install uv and Node.js LTS (winget install astral-sh.uv ; winget install OpenJS.NodeJS.LTS), open a new PowerShell window, then run this again with -Force."
  }
}

Push-Location $Project
try {
  Write-Host "Rebuilding the Python environment (uv sync) ..."
  uv sync; if ($LASTEXITCODE -ne 0) { Fail "uv sync failed." }
  Write-Host "Rebuilding the front end (npm ci, npm run build) ..."
  Push-Location web
  npm ci; if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm ci failed." }
  npm run build; if ($LASTEXITCODE -ne 0) { Pop-Location; Fail "npm run build failed." }
  Pop-Location
  Write-Host "Checking the database (should report nothing to apply) ..."
  uv run azfinops-migrate $db; if ($LASTEXITCODE -ne 0) { Fail "The migration check failed." }
  Write-Host "Running the tests ..."
  uv run pytest -q --basetemp=.pytest_tmp -p no:cacheprovider
  if ($LASTEXITCODE -ne 0) { Write-Host "Some tests failed; see above." -ForegroundColor Yellow }
} finally { Pop-Location }

$cpu = [Environment]::ProcessorCount
$ramGb = [math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ("Suggested settings for this PC ({0} logical processors, {1} GB):" -f $cpu, $ramGb)
Write-Host ("  setx AZFINOPS_THREADS {0}" -f [math]::Max(1, [math]::Floor($cpu * 0.75)))
Write-Host ("  setx AZFINOPS_MEMORY_LIMIT {0}GB" -f [math]::Max(2, [math]::Floor($ramGb * 0.375)))
Write-Host "Then open a new PowerShell window and start it:  cd `"$Project`"; uv run azfinops-serve"
