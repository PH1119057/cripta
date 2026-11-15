$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw ".venv missing; run scripts\setup_windows.ps1 first" }

Write-Host "====================================================================="
Write-Host "P47J -0.10 PROTECTION QUICK SURVIVAL"
Write-Host "Frozen Entry V1 unchanged"
Write-Host "After first +0.10: floor -0.10 versus continuation +1.10"
Write-Host "Question: how many of the +0.10-confirmed impulses remain in battle?"
Write-Host "Downloads: DISABLED / existing frozen public trades only"
Write-Host "No downstream runner/PnL replay"
Write-Host "====================================================================="

Push-Location $root
try {
    & $python -m bybit_workbench.research.early_protection_minus01_v23 `
        --project-root $root `
        --progress-interval-seconds 20
    if ($LASTEXITCODE -ne 0) {
        throw "P47J quick survival failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
