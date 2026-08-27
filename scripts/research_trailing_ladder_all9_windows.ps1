param(
    [string]$Root = "C:\cripta",
    [int]$HorizonHours = 72,
    [int]$DayCacheSize = 6,
    [double]$ProgressIntervalSeconds = 20.0
)

$ErrorActionPreference = "Stop"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Write-Host "====================================================================="
Write-Host "P47H TRAILING STOP LADDER - ALL9 EXPLORATION"
Write-Host "Frozen Entry V1: unchanged"
Write-Host "Signals: 1063 across UNI/LINK/BTC/ETH/XRP/PEPE/SOL/DOGE/ADA"
Write-Host "Initial stop: -1.00%"
Write-Host "Ladder to +1.00%: +0.10 -> BE, then stop follows in 0.10% staircase"
Write-Host "Above +1.00%: compare 0.20 / 0.25 / 0.30% staircase spacing"
Write-Host "Stop never loosens"
Write-Host "Controls: simple +1.00% and frozen full-runner GB1.50%"
Write-Host "IMPORTANT: exploratory parameter search; new five assets can be clean OOS"
Write-Host "Downloads: DISABLED / existing frozen public trades only"
Write-Host "====================================================================="

Push-Location $Root
try {
    & $python -m bybit_workbench.research.trailing_ladder_v21 `
        --root $Root `
        --horizon-hours $HorizonHours `
        --day-cache-size $DayCacheSize `
        --progress-interval-seconds $ProgressIntervalSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "P47H trailing-ladder research failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
