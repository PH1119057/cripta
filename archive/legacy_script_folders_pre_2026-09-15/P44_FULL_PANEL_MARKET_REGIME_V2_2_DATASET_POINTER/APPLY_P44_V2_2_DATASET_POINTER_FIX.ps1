param(
    [string]$ProjectRoot = "C:\cripta"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $PatchRoot "payload"
$ManifestPath = Join-Path $PatchRoot "MANIFEST.csv"

Write-Host "============================================================="
Write-Host "P44 V2.2 P30 DATASET POINTER FIX - PRECHECK"
Write-Host "Project: $ProjectRoot"
Write-Host "reports\ will NOT be modified by installer."
Write-Host "No market data will be downloaded."
Write-Host "Live trading logic will NOT be modified."
Write-Host "============================================================="

if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml"))) {
    throw "Project root does not look valid: $ProjectRoot"
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

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $python) {
    foreach ($item in $manifest) {
        if (-not ([string]$item.relative_path).EndsWith(".py")) { continue }
        $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
        $source = Join-Path $PayloadRoot $relative
        & $python -m py_compile $source
        if ($LASTEXITCODE -ne 0) { throw "Python syntax check failed: $relative" }
    }
    Get-ChildItem -Path $PayloadRoot -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Python syntax: OK"
}
else {
    Write-Warning "Project .venv Python not found; syntax precheck skipped: $python"
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $ProjectRoot "patch_backups\P44_V2_2_DATASET_POINTER_$stamp"
foreach ($item in $manifest) {
    $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    $source = Join-Path $PayloadRoot $relative
    $target = Join-Path $ProjectRoot $relative
    if (Test-Path $target) {
        $backup = Join-Path $backupRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
        Copy-Item $target $backup -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item $source $target -Force
    Write-Host "Applied: $relative"
}

Write-Host "============================================================="
Write-Host "PATCH APPLIED"
Write-Host "Backup: $backupRoot"
Write-Host ""
Write-Host "Next commands:"
Write-Host "  cd C:\cripta"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\research_market_regime_full_panel_windows.ps1"
Write-Host ""
Write-Host "V2.2 follows dataset_dir stored in P30 comparison.json."
Write-Host "Expected UNI/LINK precheck source: p30_comparison_dataset_dir"
Write-Host "============================================================="
