$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$projectRoot = (Get-Location).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "P47K: .venv missing; run scripts\setup_windows.ps1 first"
}

Write-Host "====================================================================="
Write-Host "P47K +0.50 ACTIVATION -> -0.50 FLOOR - ALL9 QUICK SURVIVAL"
Write-Host "Frozen Entry V1: unchanged"
Write-Host "Initial stop: -1.00% until first +0.50% favorable touch"
Write-Host "After +0.50%: protective floor moves to -0.50%"
Write-Host "Quick checkpoint: first -0.50% versus +1.10% continuation"
Write-Host "Special cohort: legacy 851 theoretical-BE exits"
Write-Host "Downloads: DISABLED / existing frozen public trades only"
Write-Host "No PnL or downstream runner retuning"
Write-Host "====================================================================="
& $python -m bybit_workbench.research.early_protection_plus05_minus05_v24 `
    --project-root $projectRoot `
    --progress-interval-seconds 20
if ($LASTEXITCODE -ne 0) {
    throw "P47K quick survival research failed with exit code $LASTEXITCODE"
}
