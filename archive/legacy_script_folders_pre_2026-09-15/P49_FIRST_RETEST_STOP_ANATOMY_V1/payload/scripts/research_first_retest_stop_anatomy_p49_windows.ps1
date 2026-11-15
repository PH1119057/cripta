param(
    [double]$InitialStopPct = 1.0,
    [string]$ActivationLevelsPct = "0.10,0.20,0.25,0.50",
    [double]$RetestStartDrawdownPct = 0.05,
    [double]$ReboundConfirmPct = 0.05,
    [string]$StopCandidatesPct = "-0.75,-0.50,-0.25,0.10",
    [string]$ContinuationTargetsPct = "0.50,1.00,2.00,3.00",
    [int]$HorizonHours = 72,
    [int]$DayCacheSize = 6,
    [double]$ProgressIntervalSeconds = 25.0,
    [int]$ExpectedSignals = 1063,
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

$argsList = @(
    "-m", "bybit_workbench.research.first_retest_stop_anatomy_p49",
    "--root", $root,
    "--initial-stop-pct", $InitialStopPct,
    "--activation-levels-pct", $ActivationLevelsPct,
    "--retest-start-drawdown-pct", $RetestStartDrawdownPct,
    "--rebound-confirm-pct", $ReboundConfirmPct,
    "--stop-candidates-pct", $StopCandidatesPct,
    "--continuation-targets-pct", $ContinuationTargetsPct,
    "--horizon-hours", $HorizonHours,
    "--day-cache-size", $DayCacheSize,
    "--progress-interval-seconds", $ProgressIntervalSeconds,
    "--expected-signals", $ExpectedSignals
)
if ($OutputDir) {
    $argsList += @("--output-dir", $OutputDir)
}

Write-Host "============================================================="
Write-Host "P49 FIRST RETEST / STOP TIGHTENING ANATOMY"
Write-Host "Research only. Downloads: DISABLED."
Write-Host "Entry V1 / frozen P46 / live Execution / Exit / Risk are unchanged."
Write-Host "First retest exists only AFTER a positive activation milestone."
Write-Host "Pre-activation adverse movement is Entry noise, NOT a retest."
Write-Host "Activation levels: $ActivationLevelsPct percent"
Write-Host "Retest starts after drawdown from Peak #1: $RetestStartDrawdownPct pp"
Write-Host "Retest becomes causal/confirmed after rebound from low: $ReboundConfirmPct pp"
Write-Host "Initial structural stop: -$InitialStopPct percent"
Write-Host "Candidate tightened stops: $StopCandidatesPct percent"
Write-Host "Continuation targets: $ContinuationTargetsPct percent"
Write-Host "Horizon: $HorizonHours hours"
Write-Host "Expected frozen Entry V1 signals: $ExpectedSignals"
Write-Host "Heartbeat: $ProgressIntervalSeconds sec"
Write-Host "============================================================="

Push-Location $root
try {
    & $python @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "P49 first retest anatomy failed."
    }
}
finally {
    Pop-Location
}
