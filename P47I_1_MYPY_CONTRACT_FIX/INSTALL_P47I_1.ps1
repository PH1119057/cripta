$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

function Fail([string]$Message) {
    throw "P47I.1 install aborted: $Message"
}

$projectRoot = (Get-Location).Path
if (-not (Test-Path (Join-Path $projectRoot "pyproject.toml"))) {
    Fail "run this installer from C:\cripta"
}
if (-not (Test-Path (Join-Path $projectRoot "src\bybit_workbench"))) {
    Fail "current directory does not look like C:\cripta"
}

$patchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$payloadRoot = Join-Path $patchRoot "payload"
$hashFile = Join-Path $patchRoot "SHA256SUMS.txt"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Fail ".venv is missing; run scripts\setup_windows.ps1 first" }
if (-not (Test-Path $payloadRoot)) { Fail "payload directory is missing" }
if (-not (Test-Path $hashFile)) { Fail "SHA256SUMS.txt is missing" }

# The previous P47I attempt failed in overlay precheck and must not have installed these files.
$expectedAbsent = @(
    "src\bybit_workbench\research\early_protection_differential_v22.py",
    "tests\test_early_protection_differential_v22.py",
    "scripts\research_early_protection_differential_all9_windows.ps1"
)
foreach ($relative in $expectedAbsent) {
    if (Test-Path -LiteralPath (Join-Path $projectRoot $relative)) {
        Fail "baseline mismatch: target already exists: $relative"
    }
}

Write-Host "P47I.1: verify payload hashes"
foreach ($line in Get-Content -LiteralPath $hashFile) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $parts = $line -split "  ", 2
    if ($parts.Count -ne 2) { Fail "invalid SHA256SUMS line: $line" }
    $expected = $parts[0].Trim().ToLowerInvariant()
    $relative = $parts[1].Trim().Replace("/", "\")
    $file = Join-Path $payloadRoot $relative
    if (-not (Test-Path -LiteralPath $file)) { Fail "payload file missing: $relative" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { Fail "payload SHA256 mismatch: $relative" }
}

$tempRoot = Join-Path $env:TEMP ("cripta-p47i1-overlay-" + [guid]::NewGuid().ToString("N"))
$backupRoot = $null
$oldPythonPath = $env:PYTHONPATH
$oldMypyPath = $env:MYPYPATH
$oldQt = $env:QT_QPA_PLATFORM
$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION

try {
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    Write-Host "P47I.1: create post-patch overlay"
    $exclude = @(
        ".venv", ".git", "reports", "data", "patch_backups",
        "P47I_CORRECTED_EARLY_FLOOR_QUICK",
        (Split-Path -Leaf $patchRoot)
    )
    $args = @($projectRoot, $tempRoot, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    foreach ($dir in $exclude) {
        $args += "/XD"
        $args += (Join-Path $projectRoot $dir)
    }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "robocopy overlay failed with code $LASTEXITCODE" }

    foreach ($file in Get-ChildItem -LiteralPath $payloadRoot -Recurse -File) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $tempRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    Write-Host "P47I.1: PowerShell 5.1 syntax precheck"
    $psTokens = $null
    $psErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $tempRoot "scripts\research_early_protection_differential_all9_windows.ps1"),
        [ref]$psTokens,
        [ref]$psErrors
    )
    if ($psErrors -and $psErrors.Count -gt 0) {
        $messages = ($psErrors | ForEach-Object { $_.Message }) -join "; "
        Fail ("PowerShell syntax failed: " + $messages)
    }

    Push-Location $tempRoot
    try {
        $env:PYTHONPATH = Join-Path $tempRoot "src"
        $env:MYPYPATH = Join-Path $tempRoot "src"

        Write-Host "P47I.1: py_compile"
        & $python -m py_compile `
            "src\bybit_workbench\research\early_protection_differential_v22.py" `
            "tests\test_early_protection_differential_v22.py"
        if ($LASTEXITCODE -ne 0) { Fail "py_compile failed" }

        Write-Host "P47I.1: Ruff"
        & $python -m ruff check src tests
        if ($LASTEXITCODE -ne 0) { Fail "Ruff failed" }

        Write-Host "P47I.1: mypy"
        & $python -m mypy src\bybit_workbench
        if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }

        Write-Host "P47I.1: targeted pytest"
        & $python -m pytest -q tests\test_early_protection_differential_v22.py
        if ($LASTEXITCODE -ne 0) { Fail "targeted pytest failed" }

        Write-Host "P47I.1: full pytest"
        & $python -m pytest
        if ($LASTEXITCODE -ne 0) { Fail "full pytest failed" }

        Write-Host "P47I.1: headless smoke"
        $smokeDb = Join-Path $tempRoot "p47i1-smoke.db"
        & $python -m bybit_workbench --headless --database $smokeDb
        if ($LASTEXITCODE -ne 0) { Fail "headless smoke failed" }

        Write-Host "P47I.1: GUI smoke (offscreen, replay/disarmed)"
        $env:QT_QPA_PLATFORM = "offscreen"
        $env:BYBIT_WORKBENCH_PROFILE = "replay"
        $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"
        $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"
        & $python -m bybit_workbench --gui-smoke
        if ($LASTEXITCODE -ne 0) { Fail "GUI smoke failed" }
    }
    finally {
        Pop-Location
    }

    Write-Host "P47I.1: precheck green; now applying real files"
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupRoot = Join-Path $projectRoot ("patch_backups\P47I_1_MYPY_FIX_" + $stamp)
    foreach ($file in Get-ChildItem -LiteralPath $payloadRoot -Recurse -File) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $projectRoot $relative
        if (Test-Path -LiteralPath $target) {
            $backup = Join-Path $backupRoot $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
            Copy-Item -LiteralPath $target -Destination $backup -Force
        }
    }
    foreach ($file in Get-ChildItem -LiteralPath $payloadRoot -Recurse -File) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $projectRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    Write-Host "P47I.1 installed successfully."
    if ($backupRoot -and (Test-Path $backupRoot)) { Write-Host "Backup: $backupRoot" }
    Write-Host "Authoritative gate:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
}
finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:MYPYPATH = $oldMypyPath
    $env:QT_QPA_PLATFORM = $oldQt
    $env:BYBIT_WORKBENCH_PROFILE = $oldProfile
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = $oldAllowLive
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = $oldTestnet
    if (Test-Path $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
