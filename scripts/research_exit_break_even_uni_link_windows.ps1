param(
    [double]$InitialStopPct = 1.0,
    [int]$HorizonHours = 72,
    [string]$ActivationR = "0.25,0.35,0.50,0.75,1.00",
    [string]$BreakEvenBufferBps = "0,5,10,15,20",
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
    "-m", "bybit_workbench.research.exit_break_even_v12",
    "--root", $root,
    "--initial-stop-pct", $InitialStopPct,
    "--horizon-hours", $HorizonHours,
    "--activation-r", $ActivationR,
    "--be-buffer-bps", $BreakEvenBufferBps
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
Write-Host "P45 EXIT RESEARCH V1 - BREAK-EVEN TIMING"
Write-Host "Entry V1 stays frozen. Research only."
Write-Host "Initial stop: -$InitialStopPct% price"
Write-Host "Path horizon: $HorizonHours hours"
Write-Host "Activation R grid: $ActivationR"
Write-Host "Break-even buffer grid: $BreakEvenBufferBps bps"
Write-Host "============================================================="

Push-Location $root
try {
    & $python @argsList
    if ($LASTEXITCODE -ne 0) {
        throw "P45 Exit Research V1 failed."
    }
}
finally {
    Pop-Location
}
