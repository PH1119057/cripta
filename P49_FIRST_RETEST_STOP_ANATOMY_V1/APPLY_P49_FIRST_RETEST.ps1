param(
    [string]$ProjectRoot = "C:\cripta"
)

$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $PatchRoot "payload"
$ManifestPath = Join-Path $PatchRoot "MANIFEST.csv"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "============================================================="
Write-Host "P49 FIRST RETEST / STOP TIGHTENING ANATOMY V1 - PRECHECK"
Write-Host "Project: $ProjectRoot"
Write-Host "Research only. Downloads: DISABLED."
Write-Host "Adds first-retest depth + causal stop-tightening research."
Write-Host "Entry V1 / frozen P46 / live Execution / Exit / Risk are NOT modified."
Write-Host "reports\ and market data are NOT modified by installer."
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
$PathEngine = Join-Path $ProjectRoot "src\bybit_workbench\research\exit_break_even_v13.py"
$P40Probe = Join-Path $ProjectRoot "src\bybit_workbench\research\early_failure_puncture_v20.py"
if (-not (Test-Path $CurrentInit -PathType Leaf)) {
    throw "Project package baseline missing: $CurrentInit"
}
if (-not (Test-Path $PathEngine -PathType Leaf)) {
    throw "Required raw-trade path engine missing: $PathEngine"
}
if (-not (Test-Path $P40Probe -PathType Leaf)) {
    throw "Required original-9 research baseline missing: $P40Probe"
}
$InitText = Get-Content $CurrentInit -Raw
if (-not $InitText.Contains('__version__ = "0.8.5"')) {
    throw "Unexpected project version. P49 V1 baseline requires bybit-workbench 0.8.5."
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
    "p49_first_retest_overlay_" + [Guid]::NewGuid().ToString("N")
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
            "tests\test_first_retest_stop_anatomy_p49.py" `
            "tests\test_retest_anatomy_v14.py" `
            "tests\test_early_failure_puncture_v20.py" `
            "tests\test_exit_break_even_v13.py"
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
$BackupRoot = Join-Path $ProjectRoot "patch_backups\P49_FIRST_RETEST_$Timestamp"
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
Write-Host "Resulting research revision: P49 FIRST RETEST STOP ANATOMY V1"
Write-Host "Package version remains bybit-workbench 0.8.5."
Write-Host "No report or market-data file was changed by installer."
Write-Host ""
Write-Host "Next authoritative gate:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host ""
Write-Host "Then run P49:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\research_first_retest_stop_anatomy_p49_windows.ps1"
Write-Host "============================================================="
