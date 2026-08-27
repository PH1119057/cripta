param(
    [string]$ProjectRoot = "C:\cripta"
)

$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $PatchRoot "payload"
$ManifestPath = Join-Path $PatchRoot "MANIFEST.csv"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "============================================================="
Write-Host "ENTRY BOT P48.2.1 LIVE AUDIT + SHADOW PRE-LIMIT - RUFF FIX - PRECHECK"
Write-Host "Project: $ProjectRoot"
Write-Host "Adds mandatory append-only Entry candidate history (SQLite schema v9)."
Write-Host "Adds red/yellow/green distance visualization."
Write-Host "Adds SHADOW pre-limit audit only; NO exchange order is sent."
Write-Host "Auto Mainnet Entry remains LOCKED."
Write-Host "P46 / Exit / Risk logic will NOT be modified."
Write-Host "reports\ and market data will NOT be modified by installer."
Write-Host "============================================================="

if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml") -PathType Leaf)) {
    throw "pyproject.toml not found under $ProjectRoot"
}
if (-not (Test-Path $Python -PathType Leaf)) {
    throw "Python venv not found: $Python"
}
if (-not (Test-Path $ManifestPath -PathType Leaf)) {
    throw "Manifest not found: $ManifestPath"
}

$CurrentInit = Join-Path $ProjectRoot "src\bybit_workbench\__init__.py"
$CurrentEngine = Join-Path $ProjectRoot "src\bybit_workbench\entry_bot\engine.py"
if (-not (Test-Path $CurrentInit -PathType Leaf) -or -not (Test-Path $CurrentEngine -PathType Leaf)) {
    throw "P48 Entry Bot baseline is missing. Install P48 V1.7 first."
}
$InitText = Get-Content $CurrentInit -Raw
$EngineText = Get-Content $CurrentEngine -Raw
if (-not $InitText.Contains('__version__ = "0.8.5"')) {
    throw "Unexpected project version. P48.2 baseline requires bybit-workbench 0.8.5."
}
if (-not $EngineText.Contains("_flow_window_progress")) {
    throw "P48 V1.7 causal tape warm-up baseline is missing."
}

$Rows = @(Import-Csv $ManifestPath)
if ($Rows.Count -eq 0) {
    throw "Manifest is empty."
}
Write-Host "Patch files: $($Rows.Count)"

foreach ($Row in $Rows) {
    $Relative = [string]$Row.Path
    if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative.Contains("..")) {
        throw "Unsafe manifest path: $Relative"
    }
    $Source = Join-Path $PayloadRoot $Relative
    if (-not (Test-Path $Source -PathType Leaf)) {
        throw "Payload file missing: $Relative"
    }
    $Actual = (Get-FileHash -Algorithm SHA256 $Source).Hash.ToLowerInvariant()
    $Expected = ([string]$Row.SHA256).Trim().ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "Payload hash mismatch: $Relative"
    }
}
Write-Host "Payload hashes: OK"

$PowerShellFiles = @(Get-ChildItem $PayloadRoot -Recurse -File -Filter "*.ps1")
foreach ($File in $PowerShellFiles) {
    $Tokens = $null
    $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $File.FullName,
        [ref]$Tokens,
        [ref]$Errors
    ) | Out-Null
    if ($Errors.Count -gt 0) {
        throw "PowerShell syntax failed: $($File.FullName): $($Errors[0].Message)"
    }
}
Write-Host "PowerShell syntax: OK"

$PythonFiles = @(Get-ChildItem $PayloadRoot -Recurse -File -Filter "*.py")
foreach ($File in $PythonFiles) {
    & $Python -m py_compile $File.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "Python syntax failed: $($File.FullName)"
    }
}
Write-Host "Python syntax: OK"

$Stage = Join-Path ([System.IO.Path]::GetTempPath()) (
    "entry_bot_p48_2_overlay_" + [Guid]::NewGuid().ToString("N")
)
$PreviousMypyPath = $env:MYPYPATH
$PreviousPythonPath = $env:PYTHONPATH
try {
    New-Item -ItemType Directory -Path $Stage -Force | Out-Null
    Copy-Item (Join-Path $ProjectRoot "src") (Join-Path $Stage "src") -Recurse -Force
    Copy-Item (Join-Path $ProjectRoot "tests") (Join-Path $Stage "tests") -Recurse -Force

    foreach ($Row in $Rows) {
        $Relative = ([string]$Row.Path).Replace("/", "\")
        if (-not ($Relative.StartsWith("src\") -or $Relative.StartsWith("tests\"))) {
            continue
        }
        $Source = Join-Path $PayloadRoot $Relative
        $StageTarget = Join-Path $Stage $Relative
        $StageParent = Split-Path -Parent $StageTarget
        New-Item -ItemType Directory -Path $StageParent -Force | Out-Null
        Copy-Item $Source $StageTarget -Force
    }

    Push-Location $Stage
    try {
        & $Python -m ruff check --config (Join-Path $ProjectRoot "pyproject.toml") src tests
        if ($LASTEXITCODE -ne 0) {
            throw "Ruff full-overlay precheck failed; project files were not modified."
        }
        Write-Host "Ruff full-overlay precheck: OK"

        $env:MYPYPATH = (Join-Path $Stage "src")
        & $Python -m mypy --config-file (Join-Path $ProjectRoot "pyproject.toml") `
            (Join-Path $Stage "src\bybit_workbench")
        if ($LASTEXITCODE -ne 0) {
            throw "mypy full-overlay precheck failed; project files were not modified."
        }
        Write-Host "mypy full-overlay precheck: OK"

        $env:PYTHONPATH = (Join-Path $Stage "src")
        & $Python -m pytest -q -c (Join-Path $ProjectRoot "pyproject.toml") `
            "tests\test_entry_bot_live_scanner.py" `
            "tests\test_config.py" `
            "tests\test_gui_smoke.py" `
            "tests\test_event_journal.py" `
            "tests\test_trading_journal.py"
        if ($LASTEXITCODE -ne 0) {
            throw "Targeted pytest overlay precheck failed; project files were not modified."
        }
        Write-Host "Targeted pytest overlay precheck: OK"
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:MYPYPATH = $PreviousMypyPath
    $env:PYTHONPATH = $PreviousPythonPath
    if (Test-Path $Stage) {
        Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupRoot = Join-Path $ProjectRoot "patch_backups\ENTRY_BOT_P48_2_1_$Timestamp"
$BackedUp = 0

foreach ($Row in $Rows) {
    $Relative = ([string]$Row.Path).Replace("/", "\")
    $Target = Join-Path $ProjectRoot $Relative
    if (-not (Test-Path $Target -PathType Leaf)) {
        continue
    }
    $Backup = Join-Path $BackupRoot $Relative
    $BackupParent = Split-Path -Parent $Backup
    New-Item -ItemType Directory -Path $BackupParent -Force | Out-Null
    Copy-Item $Target $Backup -Force
    $BackedUp += 1
}

foreach ($Row in $Rows) {
    $Relative = ([string]$Row.Path).Replace("/", "\")
    $Source = Join-Path $PayloadRoot $Relative
    $Target = Join-Path $ProjectRoot $Relative
    $TargetParent = Split-Path -Parent $Target
    New-Item -ItemType Directory -Path $TargetParent -Force | Out-Null
    Copy-Item $Source $Target -Force
    Write-Host "Applied: $Relative"
}

Write-Host "============================================================="
Write-Host "PATCH APPLIED"
if ($BackedUp -gt 0) {
    Write-Host "Backup: $BackupRoot ($BackedUp existing files)"
}
Write-Host "Resulting build: v0.8.5 - P48.2 (package revision P48.2.1)"
Write-Host "SQLite schema v9 is applied only when the application/database is opened."
Write-Host "The migration only adds entry_bot_candidate_events; existing rows are preserved."
Write-Host ""
Write-Host "Next:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host ""
Write-Host "Then restart Workbench and run BOT MODE screening."
Write-Host "Distance colors: red=far, yellow=watch, green=approach."
Write-Host "Audit is mandatory. A DB write failure prevents normal scanner progress."
Write-Host "No real pre-limit or Auto Entry order is sent in P48.2."
Write-Host "============================================================="
