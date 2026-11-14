param(
    [string]$ProjectRoot = "C:\cripta",
    [string]$P50Dir = "",
    [string]$ExactBaselineDir = "",
    [string]$OutputDir = "",
    [string]$MfeMilestonesPct = "0.25,0.50,0.75,1.00",
    [string]$StopCandidatesPct = "-0.75,-0.60,-0.50",
    [string]$ContinuationTargetsPct = "1.10,2.00,3.00",
    [int]$HorizonHours = 72,
    [int]$DayCacheSize = 4,
    [double]$HeartbeatSeconds = 25.0
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python -PathType Leaf)) {
    throw "Python venv not found: $Python"
}
if ([string]::IsNullOrWhiteSpace($P50Dir)) {
    $P50Dir = Join-Path $ProjectRoot "reports\multi_retest_entry_recross_p50\ALL9_P50_WORKING"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "reports\mfe_activated_risk_p51\ALL9_P51_WORKING"
}

Write-Host "============================================================="
Write-Host "P51 MFE-ACTIVATED RISK / GIVEBACK ANATOMY"
Write-Host "Research only. Downloads: DISABLED."
Write-Host "Fixed cohort: 995 Entry signals that reached +0.10 before -1.00."
Write-Host "MFE milestones: $MfeMilestonesPct percent"
Write-Host "Candidate tightened stops: $StopCandidatesPct percent"
Write-Host "Future targets: $ContinuationTargetsPct percent"
Write-Host "Primary action: first causal +0.10 Entry recovery after MFE milestone."
Write-Host "Secondary descriptive matrix: recovery #1..#6 after each MFE milestone."
Write-Host "Exact untouched +1.10 / -1.00 baseline is mandatory and reconciled."
Write-Host "Horizon: $HorizonHours hours"
Write-Host "Heartbeat: $HeartbeatSeconds sec"
Write-Host "Resume directory: $OutputDir"
Write-Host "Reserved five OOS assets: NOT TOUCHED"
Write-Host "Entry V1 / frozen P46 / live Execution / Exit / Risk unchanged."
Write-Host "============================================================="

$Arguments = @(
    "-m", "bybit_workbench.research.mfe_activated_risk_p51",
    "--root", $ProjectRoot,
    "--p50-dir", $P50Dir,
    "--output-dir", $OutputDir,
    "--mfe-milestones-pct=$MfeMilestonesPct",
    "--stop-candidates-pct=$StopCandidatesPct",
    "--continuation-targets-pct=$ContinuationTargetsPct",
    "--horizon-hours", ([string]$HorizonHours),
    "--day-cache-size", ([string]$DayCacheSize),
    "--progress-interval-seconds", ([string]$HeartbeatSeconds)
)
if (-not [string]::IsNullOrWhiteSpace($ExactBaselineDir)) {
    $Arguments += @("--exact-baseline-dir", $ExactBaselineDir)
}

Push-Location $ProjectRoot
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "P51 MFE-activated risk / giveback research failed."
    }
}
finally {
    Pop-Location
}
