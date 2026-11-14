$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw ".venv is missing. Run scripts\setup_windows.ps1 first."
}

Write-Host "====================================================================="
Write-Host "P47I CORRECTED +0.10 EARLY FLOOR - QUICK DIFFERENTIAL"
Write-Host "Frozen Entry V1 / P46: unchanged"
Write-Host "Baseline: latest completed P47H ALL9 report"
Write-Host "Old research: +0.10 activation -> 0.00 theoretical floor"
Write-Host "Corrected:    +0.10 activation -> +0.10 theoretical floor"
Write-Host "Only 142 old +1.10 candidates + 4 data-end cases are rescanned"
Write-Host "Downstream Full Runner GB1.50 result is reused after +1.10"
Write-Host "Downloads: DISABLED / existing frozen public trades only"
Write-Host "====================================================================="

& .\.venv\Scripts\python.exe -m bybit_workbench.research.early_protection_differential_v22 `
    --project-root .
if ($LASTEXITCODE -ne 0) {
    throw "P47I corrected early-floor differential failed with exit code $LASTEXITCODE"
}
