param(
    [string]$ProjectRoot = "C:\cripta",
    [string]$OutputDir = "",
    [int]$DayCacheSize = 4,
    [double]$HeartbeatSeconds = 25.0
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python -PathType Leaf)) { throw "Python venv not found: $Python" }
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "reports\entry_1m_displacement_p53\ALL9_P53_WORKING"
}

Write-Host "============================================================="
Write-Host "P53 1M ENTRY DISPLACEMENT V1.2"
Write-Host "Research only. Downloads: DISABLED. NEW5 / P46: NOT TOUCHED."
Write-Host "Baseline: exactly 1063 frozen Entry V1 exact-touch signals."
Write-Host "Baseline geometry: frozen 15m + 5m zones are recreated first."
Write-Host "1m layer: local public-trade tape -> causal 1m OHLC; zero-trade minutes are flat carry-forward bars."
Write-Host "Equivalence gate: trade-derived 1m must reproduce frozen 5m OHLCV."
Write-Host "Primary snapshot: completed 1m bars before exact-touch minute."
Write-Host "Control snapshot: completed 1m bars before frozen 5m candidate bar."
Write-Host "Shift sign: negative = deeper; positive = outward."
Write-Host "LONG deeper = lower price. SHORT deeper = higher price."
Write-Host "1m parameters are transferred from frozen 5m; NO optimizer."
Write-Host "Deeper-price availability: exact public-trade replay for 3h."
Write-Host "Entry / Exit / Risk / Execution / live runtime: UNCHANGED."
Write-Host "Output: $OutputDir"
Write-Host "Heartbeat: $HeartbeatSeconds sec"
Write-Host "============================================================="

$Arguments = @(
    "-m", "bybit_workbench.research.entry_one_minute_displacement_p53",
    "--root", $ProjectRoot,
    "--output-dir", $OutputDir,
    "--expected-signals", "1063",
    "--horizon-hours", "3",
    "--day-cache-size", ([string]$DayCacheSize),
    "--progress-interval-seconds", ([string]$HeartbeatSeconds)
)

Push-Location $ProjectRoot
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "P53 research failed." }
}
finally { Pop-Location }
