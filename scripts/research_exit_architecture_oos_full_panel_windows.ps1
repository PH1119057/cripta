param(
    [string]$Root = "C:\cripta",
    [int]$DayCacheSize = 6,
    [double]$ProgressIntervalSeconds = 25
)

$ErrorActionPreference = "Stop"
Set-Location $Root

Write-Host "====================================================================="
Write-Host "P47F FROZEN EXIT ARCHITECTURE OUT-OF-SAMPLE COMPARISON"
Write-Host "Holdout: BTC, ETH, XRP, 1000PEPE, SOL, DOGE, ADA"
Write-Host "Entry V1: frozen / unchanged"
Write-Host "Common prefix: -1% SL -> +0.10% BE -> +1.10% architecture point"
Write-Host "A: SIMPLE 100% take at modeled +1.00%"
Write-Host "B: FULL RUNNER 100%, floor +1.00%, MFE giveback 1.50%"
Write-Host "C: SPLIT 50/50, core +1.00%, runner BE floor, MFE giveback 4.00%"
Write-Host "No parameter tuning. Downloads disabled; existing frozen reports only."
Write-Host "Each 72h path is built once and all three policies are replayed on it."
Write-Host "====================================================================="

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
$output = Join-Path $Root "reports\exit_architecture_oos_v1\HOLDOUT7_$stamp"

& ".\.venv\Scripts\python.exe" -m bybit_workbench.research.exit_architecture_oos_v19 `
    --root $Root `
    --output-dir $output `
    --day-cache-size $DayCacheSize `
    --progress-interval-seconds $ProgressIntervalSeconds

if ($LASTEXITCODE -ne 0) {
    throw "P47F exit architecture OOS comparison failed with exit code $LASTEXITCODE"
}

Write-Host "P47F completed: $output"
