$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw ".venv is missing. Run scripts\setup_windows.ps1 first."
}

$smokeDatabase = Join-Path $env:TEMP "bybit-workbench-pass8-source-smoke.db"
Remove-Item $smokeDatabase -ErrorAction SilentlyContinue
Remove-Item "$smokeDatabase-shm" -ErrorAction SilentlyContinue
Remove-Item "$smokeDatabase-wal" -ErrorAction SilentlyContinue

& .\.venv\Scripts\python.exe -m py_compile research\server\monitoring\opportunity_tracker.py scripts\ruff_ratchet.py
if ($LASTEXITCODE -ne 0) { throw "Recovery source compile failed." }
& .\.venv\Scripts\python.exe scripts\ruff_ratchet.py
if ($LASTEXITCODE -ne 0) { throw "Ruff ratchet failed." }
& .\.venv\Scripts\python.exe -m mypy src\bybit_workbench
if ($LASTEXITCODE -ne 0) { throw "mypy failed." }
& .\.venv\Scripts\python.exe -m pytest
if ($LASTEXITCODE -ne 0) { throw "pytest failed." }
& .\.venv\Scripts\python.exe -m bybit_workbench --headless --database $smokeDatabase
if ($LASTEXITCODE -ne 0) { throw "Headless smoke failed." }

$oldQt = $env:QT_QPA_PLATFORM
$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION
try {
    $env:QT_QPA_PLATFORM = "offscreen"
    $env:BYBIT_WORKBENCH_PROFILE = "replay"
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"
    & .\.venv\Scripts\python.exe -m bybit_workbench --gui-smoke
    if ($LASTEXITCODE -ne 0) { throw "Source GUI smoke failed." }
}
finally {
    $env:QT_QPA_PLATFORM = $oldQt
    $env:BYBIT_WORKBENCH_PROFILE = $oldProfile
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = $oldAllowLive
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = $oldTestnet
    Remove-Item $smokeDatabase -ErrorAction SilentlyContinue
    Remove-Item "$smokeDatabase-shm" -ErrorAction SilentlyContinue
    Remove-Item "$smokeDatabase-wal" -ErrorAction SilentlyContinue
}

Write-Host "PASS 8 source verification completed successfully."
