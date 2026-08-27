$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

Write-Host "====================================================================="
Write-Host "P47E FROZEN CLOSED-1H HYPOTHESIS OUT-OF-SAMPLE VALIDATION"
Write-Host "Development assets EXCLUDED: UNIUSDT, LINKUSDT"
Write-Host "Holdout: BTCUSDT, ETHUSDT, XRPUSDT, 1000PEPEUSDT, SOLUSDT, DOGEUSDT, ADAUSDT"
Write-Host "Frozen Entry V1: unchanged"
Write-Host "Frozen Exit: +0.10% -> BE -> +1.10% -> 50/50 core/runner"
Write-Host "Core: +1.00%; runner floor: BE; MFE giveback: 4.00%"
Write-Host "Frozen H1 definition: closed-only structure + EMA20 position + EMA20 slope"
Write-Host "H1: runner-added trades predominantly occur AGAINST strict closed 1H trend"
Write-Host "Downloads: DISABLED / existing frozen reports only"
Write-Host "====================================================================="


& .\.venv\Scripts\python.exe -m bybit_workbench.research.hourly_trend_oos_v18 `
    --root $Root `
    --period-tag "20260518_20260816" `
    --symbols "BTCUSDT,ETHUSDT,XRPUSDT,1000PEPEUSDT,SOLUSDT,DOGEUSDT,ADAUSDT" `
    --ema-period 20 `
    --progress-interval-seconds 25

if ($LASTEXITCODE -ne 0) {
    throw "P47E holdout 1H validation failed with exit code $LASTEXITCODE"
}
