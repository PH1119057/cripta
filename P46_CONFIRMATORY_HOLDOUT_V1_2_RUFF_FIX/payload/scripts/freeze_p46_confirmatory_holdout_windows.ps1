$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Write-Host "====================================================================="
Write-Host "P46 CONFIRMATORY HOLDOUT - FREEZE"
Write-Host "Holdout: 2026-08-19 00:00 UTC -> 2026-09-18 00:00 UTC"
Write-Host "This command freezes thresholds and candidate criteria only."
Write-Host "It does NOT inspect holdout outcomes and does NOT download data."
Write-Host "====================================================================="

& $python -m bybit_workbench.research.confirmatory_holdout_p46 freeze --root $root
if ($LASTEXITCODE -ne 0) {
    throw "P46 freeze failed with exit code $LASTEXITCODE"
}
