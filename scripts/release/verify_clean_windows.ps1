$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Set-Location $PSScriptRoot

$exe = Join-Path $PSScriptRoot "BybitStrategyWorkbench.exe"
$checksumFile = Join-Path $PSScriptRoot "BybitStrategyWorkbench.exe.sha256"
$smokeDb = Join-Path $env:TEMP "bybit-workbench-clean-windows-smoke.db"

if (-not [Environment]::Is64BitOperatingSystem) { throw "Windows x64 is required." }
if (-not (Test-Path $exe -PathType Leaf)) { throw "BybitStrategyWorkbench.exe is missing." }
if (-not (Test-Path $checksumFile -PathType Leaf)) { throw "EXE checksum file is missing." }

$expected = ((Get-Content $checksumFile -Raw).Trim() -split '\s+')[0].ToLowerInvariant()
$actual = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "EXE SHA-256 mismatch." }

$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION
$oldDbPath = $env:BYBIT_WORKBENCH_DB_PATH
$oldQt = $env:QT_QPA_PLATFORM
try {
    $env:BYBIT_WORKBENCH_PROFILE = "replay"
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"
    $env:BYBIT_WORKBENCH_DB_PATH = $smokeDb

    Remove-Item $smokeDb -ErrorAction SilentlyContinue
    Remove-Item "$smokeDb-shm" -ErrorAction SilentlyContinue
    Remove-Item "$smokeDb-wal" -ErrorAction SilentlyContinue

    $process = Start-Process -FilePath $exe -ArgumentList @("--headless") -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Packaged headless smoke failed with exit code $($process.ExitCode)." }
    if (-not (Test-Path $smokeDb)) { throw "Packaged headless smoke did not create its isolated DB." }

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
    Remove-Item $smokeDb -ErrorAction SilentlyContinue
    Remove-Item "$smokeDb-shm" -ErrorAction SilentlyContinue
    Remove-Item "$smokeDb-wal" -ErrorAction SilentlyContinue
}

Write-Host "CLEAN WINDOWS RELEASE SMOKE PASSED."
