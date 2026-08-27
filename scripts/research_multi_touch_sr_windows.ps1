$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

Write-Host "====================================================================="
Write-Host "P45 MULTI-TOUCH SUPPORT / RESISTANCE ZONES"
Write-Host "Frozen local 15m + completed P40 Core signals only."
Write-Host "No market-data downloads. No live trading logic changes."
Write-Host "S1 = calibration for continuous feature quartiles; S2+S3 = OOS transfer."
Write-Host "====================================================================="

$env:PYTHONUNBUFFERED = "1"
& $Python -u -m bybit_workbench.research.multi_touch_sr_p45 `
    --root $ProjectRoot `
    --start "2026-05-18T00:00:00+00:00" `
    --end "2026-08-16T00:00:00+00:00" `
    --calibration-days 30 `
    --force

if ($LASTEXITCODE -ne 0) {
    throw "P45 multi-touch S/R research failed with exit code $LASTEXITCODE"
}
