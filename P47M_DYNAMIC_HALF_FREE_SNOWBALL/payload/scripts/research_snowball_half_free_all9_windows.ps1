$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$projectRoot = (Get-Location).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "P47M failed: .venv missing; run from C:\cripta"
}

Write-Host "====================================================================="
Write-Host "P47M DYNAMIC 50% OF FREE DEPOSIT - ALL9 SNOWBALL REPLAY"
Write-Host "Starting bank: 100 USD"
Write-Host "Leverage: 10x"
Write-Host "Every complete signal is eligible"
Write-Host "Allocation: 50% of currently free deposit"
Write-Host "No fixed slot cap; same-symbol overlaps allowed as virtual research trades"
Write-Host "Exit benchmark: +1.10 maker / -1.00 taker / -0.50 taker"
Write-Host "Policies: pure math no-min + 6 USD practical allocation-floor sensitivity"
Write-Host "Downloads: DISABLED / compact P47K + P47G reports only"
Write-Host "Frozen Entry V1 and P46: unchanged"
Write-Host "====================================================================="

& $python -m bybit_workbench.research.snowball_allocation_v26 `
    --root $projectRoot `
    --starting-bank-usd 100 `
    --leverage 10 `
    --allocation-fraction 0.50 `
    --maker-fee-rate 0.00020 `
    --taker-fee-rate 0.00055 `
    --minimum-allocation-usd 6 `
    --timezone-offset-hours 5

if ($LASTEXITCODE -ne 0) {
    throw "P47M snowball research failed with exit code $LASTEXITCODE"
}
