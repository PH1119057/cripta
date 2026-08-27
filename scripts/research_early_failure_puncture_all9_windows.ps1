param(
    [string]$Root = "C:\cripta",
    [double]$DeepBreakPct = 3.0,
    [double]$NearZoneLevelPct = -0.10,
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
Write-Host "P47G EARLY FAILURE PUNCTURE ANATOMY"
Write-Host "Frozen Entry V1: unchanged"
Write-Host "Target: 66 genuine early failures only"
Write-Host "Initial failure: -1.00% before +0.10%"
Write-Host "Deep-break threshold: -$DeepBreakPct%"
Write-Host "Near-entry zone recovery: $NearZoneLevelPct%"
Write-Host "Exact Entry recovery: 0.00%"
Write-Host "Path horizon: $HorizonHours hours"
Write-Host "LONG/SHORT: direction-normalized"
Write-Host "Downloads: DISABLED / existing frozen public trades only"
Write-Host "====================================================================="

Push-Location $Root
try {
    & $python -m bybit_workbench.research.early_failure_puncture_v20 `
        --root $Root `
        --deep-break-pct $DeepBreakPct `
        --near-zone-level-pct $NearZoneLevelPct `
        --horizon-hours $HorizonHours `
        --day-cache-size $DayCacheSize `
        --progress-interval-seconds $ProgressIntervalSeconds
    if ($LASTEXITCODE -ne 0) {
        throw "P47G early-failure puncture analysis failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
