param(
    [string]$ProjectRoot = "C:\cripta",
    [string]$Period = "20260518_20260816",
    [switch]$RequireAll
)

$ErrorActionPreference = "Stop"
Set-Location $ProjectRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}

$Args = @(
    "-m", "bybit_workbench.entry_bot.calibration",
    "--project-root", $ProjectRoot,
    "--period", $Period,
    "--output", "var\entry_bot_calibration.json"
)
if ($RequireAll) {
    $Args += "--require-all"
}

& $Python @Args
if ($LASTEXITCODE -ne 0) {
    throw "Entry Bot calibration build failed."
}
