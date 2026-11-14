param(
    [string]$ProjectRoot = "C:\cripta",
    [string]$Start = "2026-05-18T00:00:00+00:00",
    [string]$End = "2026-08-16T00:00:00+00:00",
    [int]$CalibrationDays = 30,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "Project Python not found: $python"
}

Write-Host "====================================================================="
Write-Host "P44 FULL PANEL MARKET REGIME V2"
Write-Host "Frozen local data only. Network downloads are NOT used."
Write-Host "S1 = calibration; S2+S3 = primary OOS evaluation."
Write-Host "Live trading logic is NOT changed by this script."
Write-Host "====================================================================="

$env:PYTHONUNBUFFERED = "1"
$arguments = @(
    "-m", "bybit_workbench.research.market_regime_full_panel_v2",
    "--root", $ProjectRoot,
    "--start", $Start,
    "--end", $End,
    "--calibration-days", $CalibrationDays
)
if ($Force) {
    $arguments += "--force"
}

Push-Location $ProjectRoot
try {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "P44 full-panel market-regime research failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
