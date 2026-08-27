param(
    [string]$ProjectRoot = "C:\cripta"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $PatchRoot "payload"
$ManifestPath = Join-Path $PatchRoot "MANIFEST.csv"

Write-Host "============================================================="
Write-Host "P45.1 CLEAN ZONE LIFECYCLE - PRECHECK"
Write-Host "Project: $ProjectRoot"
Write-Host "reports\ will NOT be modified by installer."
Write-Host "No market data will be downloaded."
Write-Host "Live trading / Exit / Risk logic will NOT be modified."
Write-Host "============================================================="

if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml"))) {
    throw "Project root does not look valid: $ProjectRoot"
}
if (-not (Test-Path (Join-Path $ProjectRoot "src\bybit_workbench\research\multi_touch_sr_p45.py"))) {
    throw "P45 dependency is missing. Install/run P45 before P45.1."
}
if (-not (Test-Path $ManifestPath)) { throw "Manifest not found: $ManifestPath" }
if (-not (Test-Path $PayloadRoot)) { throw "Payload not found: $PayloadRoot" }

$manifest = @(Import-Csv $ManifestPath)
if ($manifest.Count -eq 0) { throw "Manifest is empty." }
Write-Host "Patch files: $($manifest.Count)"

foreach ($item in $manifest) {
    $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    $source = Join-Path $PayloadRoot $relative
    if (-not (Test-Path $source)) { throw "Payload file missing: $relative" }
    $actual = (Get-FileHash -Algorithm SHA256 $source).Hash.ToLowerInvariant()
    $expected = ([string]$item.sha256).ToLowerInvariant()
    if ($actual -ne $expected) { throw "Payload hash mismatch: $relative" }
}
Write-Host "Payload hashes: OK"

foreach ($item in $manifest) {
    if (-not ([string]$item.relative_path).EndsWith(".ps1")) { continue }
    $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    $source = Join-Path $PayloadRoot $relative
    [void][scriptblock]::Create((Get-Content $source -Raw))
}
Write-Host "PowerShell syntax: OK"

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $python) {
    $pythonFiles = @()
    foreach ($item in $manifest) {
        if (-not ([string]$item.relative_path).EndsWith(".py")) { continue }
        $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
        $source = Join-Path $PayloadRoot $relative
        $pythonFiles += $source
        & $python -m py_compile $source
        if ($LASTEXITCODE -ne 0) { throw "Python syntax check failed: $relative" }
    }
    Get-ChildItem -Path $PayloadRoot -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Python syntax: OK"

    if ($pythonFiles.Count -gt 0) {
        & $python -m ruff check --config (Join-Path $ProjectRoot "pyproject.toml") @pythonFiles
        if ($LASTEXITCODE -ne 0) {
            throw "Ruff payload precheck failed; project files were not modified."
        }
        Write-Host "Ruff payload precheck: OK"
    }
}
else {
    Write-Warning "Project .venv Python not found; Python/Ruff precheck skipped: $python"
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $ProjectRoot "patch_backups\P45_1_CLEAN_ZONE_LIFECYCLE_$stamp"
$backedUp = $false
foreach ($item in $manifest) {
    $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    $source = Join-Path $PayloadRoot $relative
    $target = Join-Path $ProjectRoot $relative
    if (Test-Path $target) {
        $backup = Join-Path $backupRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
        Copy-Item $target $backup -Force
        $backedUp = $true
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item $source $target -Force
    Write-Host "Applied: $relative"
}

Write-Host "============================================================="
Write-Host "PATCH APPLIED"
if ($backedUp) {
    Write-Host "Backup: $backupRoot"
}
else {
    Write-Host "Backup: not needed; all project targets were new files"
}
Write-Host ""
Write-Host "Next commands:"
Write-Host "  cd C:\cripta"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\research_clean_zone_lifecycle_windows.ps1"
Write-Host ""
Write-Host "Expected report:"
Write-Host "  reports\clean_zone_lifecycle_p451\ENTRY_V1_20260518_20260816.zip"
Write-Host "============================================================="
