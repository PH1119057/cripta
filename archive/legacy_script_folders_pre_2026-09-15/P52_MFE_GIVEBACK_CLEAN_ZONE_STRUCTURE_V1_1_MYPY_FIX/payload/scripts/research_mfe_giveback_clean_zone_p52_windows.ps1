param(
    [string]$ProjectRoot = "C:\cripta",
    [string]$P50Dir = "",
    [string]$P45Dir = "",
    [string]$ExactBaselineDir = "",
    [string]$OutputDir = "",
    [int]$DayCacheSize = 4,
    [double]$HeartbeatSeconds = 25.0
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python -PathType Leaf)) { throw "Python venv not found: $Python" }
if ([string]::IsNullOrWhiteSpace($P50Dir)) {
    $P50Dir = Join-Path $ProjectRoot "reports\multi_retest_entry_recross_p50\ALL9_P50_WORKING"
}
if ([string]::IsNullOrWhiteSpace($P45Dir)) {
    $P45Dir = Join-Path $ProjectRoot "reports\clean_zone_lifecycle_p451\ENTRY_V1_20260518_20260816"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "reports\mfe_giveback_clean_zone_p52\ALL9_P52_WORKING"
}

Write-Host "============================================================="
Write-Host "P52 MFE + GIVEBACK + CLEAN ZONE STRUCTURE V1"
Write-Host "Research only. Downloads: DISABLED. NEW5: NOT TOUCHED."
Write-Host "Exact cohort: accepted P50/P51 +0.10 activation cohort."
Write-Host "P45.1 semantics: bounce / false-break reclaim / 2-close clean break."
Write-Host "Causal clock: zone outcome_at must be known before +1.10 / -1 outcome."
Write-Host "Early structure window: 60 minutes after +0.10 activation."
Write-Host "MFE grid: +0.25,+0.50,+0.75,+1.00"
Write-Host "Stop grid: -0.75,-0.60,-0.50 (fixed; NO optimizer)"
Write-Host "Runner controls: +1.10 / +2 / +3 before original -1."
Write-Host "Stability: direction / symbol / month."
Write-Host "Entry / Exit / Risk / Execution: UNCHANGED."
Write-Host "Output: $OutputDir"
Write-Host "============================================================="

$Arguments = @(
    "-m", "bybit_workbench.research.mfe_giveback_clean_zone_p52",
    "--root", $ProjectRoot,
    "--p50-dir", $P50Dir,
    "--p45-dir", $P45Dir,
    "--output-dir", $OutputDir,
    "--day-cache-size", ([string]$DayCacheSize),
    "--progress-interval-seconds", ([string]$HeartbeatSeconds)
)
if (-not [string]::IsNullOrWhiteSpace($ExactBaselineDir)) {
    $Arguments += @("--exact-baseline-dir", $ExactBaselineDir)
}

Push-Location $ProjectRoot
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "P52 research failed." }
}
finally { Pop-Location }
