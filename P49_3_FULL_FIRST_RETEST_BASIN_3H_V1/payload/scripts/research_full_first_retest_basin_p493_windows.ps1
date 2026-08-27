param(
    [string]$ProjectRoot = "C:\cripta",
    [string]$P49V12Dir = "",
    [string]$OutputDir = "",
    [double]$ActivationPct = 0.10,
    [double]$InitialStopPct = 1.00,
    [double]$RetestStartDrawdownPct = 0.05,
    [string]$StopCandidatesPct = "-0.75,-0.60,-0.50,-0.35,-0.25,0.10",
    [string]$ContinuationTargetsPct = "0.50,1.00,2.00,3.00",
    [string]$ThreeHourDepthsPct = "-0.25,-0.35,-0.50,-0.60,-0.75,-1.00",
    [double]$ThreeHourHours = 3.0,
    [int]$HorizonHours = 72,
    [int]$DayCacheSize = 4,
    [double]$ProgressIntervalSeconds = 25.0,
    [int]$ExpectedSignals = 1063,
    [int]$ExpectedCohort = 995
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python -PathType Leaf)) {
    throw "Python venv not found: $Python"
}
if ([string]::IsNullOrWhiteSpace($P49V12Dir)) {
    $P49V12Dir = Join-Path $ProjectRoot "reports\first_retest_stop_anatomy_p49\ALL9_P49_WORKING"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "reports\full_first_retest_basin_p493\ALL9_P493_WORKING"
}

$P49Summary = Join-Path $P49V12Dir "summary.json"
$P49Events = Join-Path $P49V12Dir "first_retest_events.csv"
if (-not (Test-Path $P49Summary -PathType Leaf)) {
    throw "Completed P49.2 summary is required: $P49Summary"
}
if (-not (Test-Path $P49Events -PathType Leaf)) {
    throw "Completed P49.2 event table is required: $P49Events"
}

Write-Host "============================================================="
Write-Host "P49.3 FULL FIRST RETEST BASIN + 3H RISK ANATOMY"
Write-Host "Research only. Downloads: DISABLED."
Write-Host "Entry V1 / frozen P46 / live Execution / Exit / Risk unchanged."
Write-Host "Fixed cohort: 995 Entry signals that reached +0.10 before -1.00."
Write-Host "The 66 early failures are excluded from this cycle."
Write-Host "Full retest ends only at Peak #1 reclaim, original -1 stop, or censoring."
Write-Host "3h depth levels: $ThreeHourDepthsPct percent"
Write-Host "Candidate tightened stops: $StopCandidatesPct percent"
Write-Host "Continuation targets: $ContinuationTargetsPct percent"
Write-Host "Horizon: $HorizonHours hours"
Write-Host "Heartbeat: $ProgressIntervalSeconds sec"
Write-Host "Resume directory: $OutputDir"
Write-Host "Reserved five OOS assets: NOT TOUCHED"
Write-Host "============================================================="

$Args = @(
    "-m", "bybit_workbench.research.full_first_retest_basin_p493",
    "--root", $ProjectRoot,
    "--p49-v12-dir", $P49V12Dir,
    "--output-dir", $OutputDir,
    "--activation-pct", ([string]$ActivationPct),
    "--initial-stop-pct", ([string]$InitialStopPct),
    "--retest-start-drawdown-pct", ([string]$RetestStartDrawdownPct),
    "--stop-candidates-pct=$StopCandidatesPct",
    "--continuation-targets-pct=$ContinuationTargetsPct",
    "--three-hour-depths-pct=$ThreeHourDepthsPct",
    "--three-hour-hours", ([string]$ThreeHourHours),
    "--horizon-hours", ([string]$HorizonHours),
    "--day-cache-size", ([string]$DayCacheSize),
    "--progress-interval-seconds", ([string]$ProgressIntervalSeconds),
    "--expected-signals", ([string]$ExpectedSignals),
    "--expected-cohort", ([string]$ExpectedCohort)
)

Push-Location $ProjectRoot
try {
    & $Python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "P49.3 full first retest basin research failed."
    }
}
finally {
    Pop-Location
}

Write-Host "============================================================="
Write-Host "P49.3 COMPLETE"
Write-Host "Main table:"
Write-Host "  $OutputDir\full_retest_depth_matrix.csv"
Write-Host "3h paths:"
Write-Host "  $OutputDir\three_hour_paths_995.csv"
Write-Host "3h -1 cases:"
Write-Host "  $OutputDir\three_hour_minus1_cases.csv"
Write-Host "Saved losers vs lost runners:"
Write-Host "  $OutputDir\retest_start_stop_tradeoff.csv"
Write-Host "Readable summary:"
Write-Host "  $OutputDir\summary.md"
Write-Host "============================================================="
