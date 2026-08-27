param(
    [double]$InitialStopPct = 1.0,
    [double]$ActivationR = 1.0,
    [double]$BreakEvenBufferBps = 0.0,
    [int]$HorizonHours = 72,
    [string]$RunnerTargetsR = "2,3,5,10",
    [string]$RecoveryLevelsR = "0,0.25,0.5,1,2,3,5,10",
    [string]$AdverseLevelsR = "0.25,0.5,1",
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
    "-m", "bybit_workbench.research.retest_anatomy_v14",
    "--root", $root,
    "--initial-stop-pct", $InitialStopPct,
    "--activation-r", $ActivationR,
    "--be-buffer-bps", $BreakEvenBufferBps,
    "--horizon-hours", $HorizonHours,
    "--runner-targets-r", $RunnerTargetsR,
    "--recovery-levels-r", $RecoveryLevelsR,
    "--adverse-levels-r", $AdverseLevelsR,
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
Write-Host "P47A RETEST ANATOMY - +1R -> BE CONTINUATION DIAGNOSTICS"
Write-Host "Entry V1 stays frozen. Research only. No re-entry is executed."
Write-Host "Initial stop: -$InitialStopPct% price = -1R"
Write-Host "Reference activation: +$ActivationR R"
Write-Host "Reference BE buffer: $BreakEvenBufferBps bps"
Write-Host "Path horizon: $HorizonHours hours"
Write-Host "Runner targets: $RunnerTargetsR R"
Write-Host "Recovery checks after BE: $RecoveryLevelsR R"
Write-Host "Adverse checks after BE: -($AdverseLevelsR) R"
Write-Host "Day cache size: $DayCacheSize"
Write-Host "Progress heartbeat: $ProgressIntervalSeconds sec"
Write-Host "============================================================="

Push-Location $root
try {
    & $python @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "P47A Retest Anatomy failed."
    }
}
finally {
    Pop-Location
}
