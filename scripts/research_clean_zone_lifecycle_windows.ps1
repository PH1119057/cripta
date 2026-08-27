$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

Write-Host "====================================================================="
Write-Host "P45.1 CLEAN SUPPORT / RESISTANCE ZONE LIFECYCLE"
Write-Host "Frozen local 15m + completed P40 Core signals only."
Write-Host "No market-data downloads. No live trading / Exit / Risk changes."
Write-Host "Confirmed break resets the phase and touch ordinal."
Write-Host "S1 calibrates continuous features; S2+S3 is transfer analysis."
Write-Host "====================================================================="

$env:PYTHONUNBUFFERED = "1"
& $Python -u -m bybit_workbench.research.clean_zone_lifecycle_p451 `
    --root $ProjectRoot `
    --start "2026-05-18T00:00:00+00:00" `
    --end "2026-08-16T00:00:00+00:00" `
    --calibration-days 30 `
    --force

if ($LASTEXITCODE -ne 0) {
    throw "P45.1 clean-zone lifecycle research failed with exit code $LASTEXITCODE"
}
