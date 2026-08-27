$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Set-Location (Split-Path -Parent $PSScriptRoot)

$version = "0.8.2"
$python = ".\.venv\Scripts\python.exe"
$exe = Join-Path (Get-Location) "dist\BybitStrategyWorkbench.exe"
$headlessDb = Join-Path $env:TEMP "bybit-workbench-pass6-packaged-headless.db"

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "Windows x64 is required."
}
if (-not [Environment]::Is64BitProcess) {
    throw "Run this release gate from a 64-bit PowerShell process."
}
if (-not (Test-Path $python)) {
    throw ".venv is missing. Run scripts\setup\_windows.ps1 first."
}

Write-Host "=== PASS 6 / 1. SOURCE GATE ==="
& $python -m ruff check src tests
if ($LASTEXITCODE -ne 0) { throw "Ruff failed." }
& $python -m mypy src\bybit_workbench
if ($LASTEXITCODE -ne 0) { throw "mypy failed." }
& $python -m pytest
if ($LASTEXITCODE -ne 0) { throw "pytest failed." }

Write-Host "=== PASS 6 / 2. OPT-IN OFFLINE SOAK ==="
$oldRunSoak = $env:RUN_SOAK_TESTS
$oldSoakCycles = $env:SOAK_CYCLES
try {
    $env:RUN_SOAK_TESTS = "1"
    $env:SOAK_CYCLES = "10000"
    & $python -m pytest -q tests\test_soak.py
    if ($LASTEXITCODE -ne 0) { throw "Offline soak failed." }
}
finally {
    $env:RUN_SOAK_TESTS = $oldRunSoak
    $env:SOAK_CYCLES = $oldSoakCycles
}

Write-Host "=== PASS 6 / 3. CLEAN ONE-FILE BUILD ==="
Remove-Item ".\build" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item ".\dist" -Recurse -Force -ErrorAction SilentlyContinue
& $python -m PyInstaller --clean --noconfirm bybit_workbench.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
if (-not (Test-Path $exe -PathType Leaf)) { throw "One-file EXE is missing." }
$unexpected = @(Get-ChildItem ".\dist" -Force | Where-Object { $_.Name -ne "BybitStrategyWorkbench.exe" })
if ($unexpected.Count -ne 0) {
    throw "PyInstaller dist must contain only the one-file EXE before packaging. Found: $($unexpected.Name -join ', ')"
}

Write-Host "=== PASS 6 / 4. PACKAGED HEADLESS SMOKE ==="
Remove-Item $headlessDb -ErrorAction SilentlyContinue
Remove-Item "$headlessDb-shm" -ErrorAction SilentlyContinue
Remove-Item "$headlessDb-wal" -ErrorAction SilentlyContinue
$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION
$oldDbPath = $env:BYBIT_WORKBENCH_DB_PATH
$oldQt = $env:QT_QPA_PLATFORM
try {
    $env:BYBIT_WORKBENCH_PROFILE = "replay"
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"
    $env:BYBIT_WORKBENCH_DB_PATH = $headlessDb
    $process = Start-Process -FilePath $exe -ArgumentList @("--headless") -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Packaged headless smoke failed with exit code $($process.ExitCode)." }
    if (-not (Test-Path $headlessDb)) { throw "Packaged headless smoke did not create its isolated audit DB." }

    Write-Host "=== PASS 6 / 5. PACKAGED GUI SMOKE ==="
    $env:QT_QPA_PLATFORM = "offscreen"
    $process = Start-Process -FilePath $exe -ArgumentList @("--gui-smoke") -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Packaged GUI smoke failed with exit code $($process.ExitCode)." }
}
finally {
    $env:BYBIT_WORKBENCH_PROFILE = $oldProfile
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = $oldAllowLive
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = $oldTestnet
    $env:BYBIT_WORKBENCH_DB_PATH = $oldDbPath
    $env:QT_QPA_PLATFORM = $oldQt
    Remove-Item $headlessDb -ErrorAction SilentlyContinue
    Remove-Item "$headlessDb-shm" -ErrorAction SilentlyContinue
    Remove-Item "$headlessDb-wal" -ErrorAction SilentlyContinue
}

Write-Host "=== PASS 6 / 6. RELEASE ARTIFACTS ==="
& $python .\scripts\release\package_release.py --root . --dist .\dist --version $version
if ($LASTEXITCODE -ne 0) { throw "Release packaging failed." }
& $python .\scripts\release\verify_release.py --dist .\dist --version $version
if ($LASTEXITCODE -ne 0) { throw "Release artifact verification failed." }

Write-Host "=== PASS 6 RESULT ==="
Get-ChildItem ".\dist" | Select-Object Name, Length | Format-Table -AutoSize
Write-Host "PASS 6 Windows release completed successfully."
