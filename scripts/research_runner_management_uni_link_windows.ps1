param(
    [double]$InitialStopPct = 1.0,
    [double]$EarlyActivationPct = 0.10,
    [double]$EarlyFloorPct = 0.0,
    [double]$RunnerActivationPct = 1.10,
    [double]$RunnerFloorPct = 1.00,
    [int]$HorizonHours = 72,
    [string]$TargetLevelsPct = "1.5,2,3,5,10",
    [string]$StepGivebackPct = "0.25,0.50,0.75,1.00",
    [string]$MfeGivebackPct = "0.25,0.50,0.75,1.00,1.50",
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
    "-m", "bybit_workbench.research.runner_management_v15",
    "--root", $root,
    "--initial-stop-pct", $InitialStopPct,
    "--early-activation-pct", $EarlyActivationPct,
    "--early-floor-pct", $EarlyFloorPct,
    "--runner-activation-pct", $RunnerActivationPct,
    "--runner-floor-pct", $RunnerFloorPct,
    "--horizon-hours", $HorizonHours,
    "--target-levels-pct", $TargetLevelsPct,
    "--step-giveback-pct", $StepGivebackPct,
    "--mfe-giveback-pct", $MfeGivebackPct,
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
Write-Host "P47B RUNNER MANAGEMENT V1 - POST +1.10% ONLY"
Write-Host "Entry V1 stays frozen. Research only. No live logic changes."
Write-Host "Initial stop: -$InitialStopPct%"
Write-Host "Frozen prefix: +$EarlyActivationPct% -> floor +$EarlyFloorPct%"
Write-Host "Runner gate: +$RunnerActivationPct% -> floor +$RunnerFloorPct%"
Write-Host "After runner gate: control / step / MFE giveback / causal structure"
Write-Host "Target checks: $TargetLevelsPct%"
Write-Host "Path horizon: $HorizonHours hours"
Write-Host "Day cache size: $DayCacheSize"
Write-Host "Progress heartbeat: $ProgressIntervalSeconds sec"
Write-Host "============================================================="

Push-Location $root
try {
    & $python @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "P47B Runner Management failed."
    }
}
finally {
    Pop-Location
}
