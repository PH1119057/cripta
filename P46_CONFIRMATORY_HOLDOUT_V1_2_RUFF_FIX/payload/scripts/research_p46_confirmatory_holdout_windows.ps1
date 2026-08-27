param([switch]$Force)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Write-Host "====================================================================="
Write-Host "P46 CONFIRMATORY HOLDOUT"
Write-Host "Holdout: 2026-08-19 00:00 UTC -> 2026-09-18 00:00 UTC"
Write-Host "Frozen thresholds only. No recalibration. No network."
Write-Host "P39/P40 orderbook is intentionally not required."
Write-Host "Live trading / Exit / Risk logic is NOT changed."
Write-Host "====================================================================="

$args = @(
    "-m", "bybit_workbench.research.confirmatory_holdout_p46",
    "evaluate", "--root", $root
)
if ($Force) { $args += "--force" }
& $python @args
if ($LASTEXITCODE -ne 0) {
    throw "P46 confirmatory holdout failed with exit code $LASTEXITCODE"
}
