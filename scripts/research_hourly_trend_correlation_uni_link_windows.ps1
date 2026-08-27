$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

Write-Host "============================================================="
Write-Host "P47D CLOSED 1H TREND CORRELATION"
Write-Host "Entry/Exit stay frozen. Diagnostic correlation only."
Write-Host "1H OHLC: rebuilt from raw public trades"
Write-Host "Current partial hour: EXCLUDED"
Write-Host "Structure: HH+HL bullish / LH+LL bearish"
Write-Host "EMA: close vs EMA20 of last fully closed 1H candle"
Write-Host "Selected P47C policy: CORE050_RUN050_BE_MFE_GB4.00"
Write-Host "Expected groups: 227 total / 16 initial stop / 27 +1.10 success / 7 runner-added"
Write-Host "============================================================="

& .\.venv\Scripts\python.exe -m bybit_workbench.research.hourly_trend_correlation_v17 `
    --root $Root `
    --selected-policy-id "CORE050_RUN050_BE_MFE_GB4.00" `
    --ema-period 20 `
    --progress-interval-seconds 25

if ($LASTEXITCODE -ne 0) {
    throw "P47D hourly trend correlation failed with exit code $LASTEXITCODE"
}
