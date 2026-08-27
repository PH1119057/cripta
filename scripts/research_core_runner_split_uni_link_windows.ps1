param(
    [double]$InitialStopPct = 1.0,
    [double]$EarlyActivationPct = 0.10,
    [double]$EarlyFloorPct = 0.0,
    [double]$SplitActivationPct = 1.10,
    [double]$CoreExitPct = 1.00,
    [string]$CoreFractions = "1.00,0.80,0.75,0.50",
    [string]$MfeGivebackPct = "1.50,2.00,2.50,3.00,4.00,5.00",
    [string]$TargetLevelsPct = "1.5,2,3,5,10",
    [int]$HorizonHours = 72,
    [int]$DayCacheSize = 6,
    [double]$ProgressIntervalSeconds = 25.0,
    [string]$UniP40Dir = "",
    [string]$LinkP40Dir = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

$argsList = @(
    "-m", "bybit_workbench.research.core_runner_split_v16",
    "--root", $root,
    "--initial-stop-pct", $InitialStopPct,
    "--early-activation-pct", $EarlyActivationPct,
    "--early-floor-pct", $EarlyFloorPct,
    "--split-activation-pct", $SplitActivationPct,
    "--core-exit-pct", $CoreExitPct,
    "--core-fractions", $CoreFractions,
    "--mfe-giveback-pct", $MfeGivebackPct,
    "--target-levels-pct", $TargetLevelsPct,
    "--horizon-hours", $HorizonHours,
    "--day-cache-size", $DayCacheSize,
    "--progress-interval-seconds", $ProgressIntervalSeconds
)
if ($UniP40Dir) {
    $argsList += @("--uni-p40-dir", $UniP40Dir)
}
if ($LinkP40Dir) {
    $argsList += @("--link-p40-dir", $LinkP40Dir)
}
if ($OutputDir) {
    $argsList += @("--output-dir", $OutputDir)
}

Write-Host "============================================================="
Write-Host "P47C CORE + RUNNER SPLIT V1"
Write-Host "Entry V1 stays frozen. Research only."
Write-Host "Initial stop: -$InitialStopPct%"
Write-Host "Frozen protection: +$EarlyActivationPct% -> floor +$EarlyFloorPct%"
Write-Host "Split gate: +$SplitActivationPct%"
Write-Host "Core valued conservatively at: +$CoreExitPct%"
Write-Host "Core fractions: $CoreFractions"
Write-Host "Runner floors: BE (no loosen) and FUNDED -1%"
Write-Host "Runner MFE givebacks: $MfeGivebackPct%"
Write-Host "Target checks: $TargetLevelsPct%"
Write-Host "Path horizon: $HorizonHours hours"
Write-Host "Day cache size: $DayCacheSize"
Write-Host "Progress heartbeat: $ProgressIntervalSeconds sec"
Write-Host "============================================================="

Push-Location $root
try {
    & $python @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "P47C Core + Runner Split failed."
    }
}
finally {
    Pop-Location
}
