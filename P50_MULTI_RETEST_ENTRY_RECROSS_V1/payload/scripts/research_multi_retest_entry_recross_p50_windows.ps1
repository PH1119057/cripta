param(
    [string]$ProjectRoot = "C:\cripta",
    [string]$P49Dir = "",
    [string]$OutputDir = "",
    [double]$ActivationPct = 0.10,
    [double]$InitialStopPct = 1.00,
    [double]$RetestDrawdownPct = 0.05,
    [string]$StopCandidatesPct = "-0.75,-0.60,-0.50,-0.35,-0.25,0.10",
    [string]$ContinuationTargetsPct = "0.50,1.00,2.00,3.00",
    [int]$HorizonHours = 72,
    [int]$DayCacheSize = 4,
    [double]$HeartbeatSeconds = 25.0
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python -PathType Leaf)) {
    throw "Python venv not found: $Python"
}
if ([string]::IsNullOrWhiteSpace($P49Dir)) {
    $P49Dir = Join-Path $ProjectRoot "reports\first_retest_stop_anatomy_p49\ALL9_P49_WORKING"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "reports\multi_retest_entry_recross_p50\ALL9_P50_WORKING"
}

Write-Host "============================================================="
Write-Host "P50 MULTI-RETEST / ENTRY RECROSS LIFECYCLE"
Write-Host "Research only. Downloads: DISABLED."
Write-Host "Fixed cohort: 995 Entry signals that reached +0.10 before -1.00."
Write-Host "Tracks repeated peak-retest cycles and distinct returns to Entry."
Write-Host "Action checkpoints: retest/recross recovery #1..#6."
Write-Host "Candidate stops: $StopCandidatesPct percent"
Write-Host "Continuation targets: $ContinuationTargetsPct percent"
Write-Host "Horizon: $HorizonHours hours"
Write-Host "Heartbeat: $HeartbeatSeconds sec (independent worker heartbeat)"
Write-Host "Resume directory: $OutputDir"
Write-Host "Reserved five OOS assets: NOT TOUCHED"
Write-Host "Entry V1 / frozen P46 / live Execution / Exit / Risk unchanged."
Write-Host "============================================================="

Push-Location $ProjectRoot
try {
    $Arguments = @(
        "-m", "bybit_workbench.research.multi_retest_entry_recross_p50",
        "--root", $ProjectRoot,
        "--p49-dir", $P49Dir,
        "--output-dir", $OutputDir,
        "--activation-pct", ([string]$ActivationPct),
        "--initial-stop-pct", ([string]$InitialStopPct),
        "--retest-drawdown-pct", ([string]$RetestDrawdownPct),
        "--stop-candidates-pct=$StopCandidatesPct",
        "--continuation-targets-pct=$ContinuationTargetsPct",
        "--horizon-hours", ([string]$HorizonHours),
        "--day-cache-size", ([string]$DayCacheSize),
        "--progress-interval-seconds", ([string]$HeartbeatSeconds),
        "--expected-signals", "1063",
        "--expected-cohort", "995"
    )
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "P50 multi-retest / Entry recross research failed."
    }
}
finally {
    Pop-Location
}
