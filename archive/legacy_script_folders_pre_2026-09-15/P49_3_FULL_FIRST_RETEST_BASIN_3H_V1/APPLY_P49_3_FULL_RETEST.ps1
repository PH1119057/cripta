param(
    [string]$ProjectRoot = "C:\cripta"
)

$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $PatchRoot "payload"
$ManifestPath = Join-Path $PatchRoot "MANIFEST.csv"
$BaselinePath = Join-Path $PatchRoot "BASELINE.csv"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "============================================================="
Write-Host "P49.3 FULL FIRST RETEST BASIN + 3H - FAIL-CLOSED PRECHECK"
Write-Host "Project: $ProjectRoot"
Write-Host "Baseline: bybit-workbench 0.8.5 + P49 V1 + P49.1 + P49.2"
Write-Host "Research only. Downloads: DISABLED."
Write-Host "Adds full-retetst basin, 3h path table, saved-losers vs lost-runners."
Write-Host "Entry V1 / frozen P46 / live Execution / Exit / Risk unchanged."
Write-Host "Reserved five OOS assets are not touched."
Write-Host "Installer does not modify reports, market data, calibration or user data."
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
if (-not (Test-Path $BaselinePath -PathType Leaf)) {
    throw "Baseline manifest not found: $BaselinePath"
}

$CurrentInit = Join-Path $ProjectRoot "src\bybit_workbench\__init__.py"
if (-not (Test-Path $CurrentInit -PathType Leaf)) {
    throw "Project package baseline missing: $CurrentInit"
}
$InitText = Get-Content $CurrentInit -Raw
if (-not $InitText.Contains('__version__ = "0.8.5"')) {
    throw "Unexpected project version. P49.3 requires bybit-workbench 0.8.5."
}

$BaselineRows = @(Import-Csv $BaselinePath)
foreach ($Row in $BaselineRows) {
    $Relative = ([string]$Row.Path).Replace("/", "\")
    if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative.Contains("..")) {
        throw "Unsafe baseline path: $Relative"
    }
    $Target = Join-Path $ProjectRoot $Relative
    if (-not (Test-Path $Target -PathType Leaf)) {
        throw "Required P49.2 baseline file missing: $Relative"
    }
    $Actual = (Get-FileHash -Algorithm SHA256 $Target).Hash.ToLowerInvariant()
    $Expected = ([string]$Row.SHA256).Trim().ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "Baseline hash mismatch: $Relative. Current C:\cripta differs from accepted P49.2; rebuild from current source."
    }
}
Write-Host "Baseline hashes: OK"

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
    if ($Relative.StartsWith("reports/") -or $Relative.StartsWith("var/") -or $Relative.StartsWith("data/")) {
        throw "Forbidden installer payload path: $Relative"
    }
    $Source = Join-Path $PayloadRoot ($Relative.Replace("/", "\"))
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
Write-Host "PowerShell payload syntax: OK"

$Stage = Join-Path ([System.IO.Path]::GetTempPath()) (
    "p49_3_full_retest_overlay_" + [Guid]::NewGuid().ToString("N")
)
$PreviousMypyPath = $env:MYPYPATH
$PreviousPythonPath = $env:PYTHONPATH
try {
    New-Item -ItemType Directory -Path $Stage -Force | Out-Null
    Copy-Item (Join-Path $ProjectRoot "src") (Join-Path $Stage "src") -Recurse -Force
    Copy-Item (Join-Path $ProjectRoot "tests") (Join-Path $Stage "tests") -Recurse -Force
    Copy-Item (Join-Path $ProjectRoot "scripts") (Join-Path $Stage "scripts") -Recurse -Force

    foreach ($Row in $Rows) {
        $Relative = ([string]$Row.Path).Replace("/", "\")
        if (-not ($Relative.StartsWith("src\") -or $Relative.StartsWith("tests\") -or $Relative.StartsWith("scripts\"))) {
            continue
        }
        $Source = Join-Path $PayloadRoot $Relative
        $StageTarget = Join-Path $Stage $Relative
        $StageParent = Split-Path -Parent $StageTarget
        New-Item -ItemType Directory -Path $StageParent -Force | Out-Null
        Copy-Item $Source $StageTarget -Force
    }

    $StageLauncher = Join-Path $Stage "scripts\research_full_first_retest_basin_p493_windows.ps1"
    $Tokens = $null
    $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $StageLauncher,
        [ref]$Tokens,
        [ref]$Errors
    ) | Out-Null
    if ($Errors.Count -gt 0) {
        throw "PowerShell post-patch overlay syntax failed: $($Errors[0].Message)"
    }
    Write-Host "PowerShell post-patch overlay syntax: OK"

    & $Python -m compileall -q (Join-Path $Stage "src") (Join-Path $Stage "tests")
    if ($LASTEXITCODE -ne 0) {
        throw "Python compileall post-patch overlay failed; project files were not modified."
    }
    Write-Host "Python compileall post-patch overlay: OK"

    Push-Location $Stage
    try {
        & $Python -m ruff check --config (Join-Path $ProjectRoot "pyproject.toml") src tests
        if ($LASTEXITCODE -ne 0) {
            throw "Ruff full-overlay precheck failed; project files were not modified."
        }
        Write-Host "Ruff full-overlay precheck: OK"

        $env:MYPYPATH = (Join-Path $Stage "src")
        & $Python -m mypy --config-file (Join-Path $ProjectRoot "pyproject.toml") (Join-Path $Stage "src\bybit_workbench")
        if ($LASTEXITCODE -ne 0) {
            throw "mypy full-overlay precheck failed; project files were not modified."
        }
        Write-Host "mypy full-overlay precheck: OK"

        $env:PYTHONPATH = (Join-Path $Stage "src")
        & $Python -m pytest -q -c (Join-Path $ProjectRoot "pyproject.toml") `
            "tests\test_full_first_retest_basin_p493.py" `
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
$BackupRoot = Join-Path $ProjectRoot "patch_backups\P49_3_FULL_RETEST_$Timestamp"
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
Write-Host "Resulting research revision: P49.3 FULL FIRST RETEST BASIN + 3H V1"
Write-Host "Package version remains bybit-workbench 0.8.5."
Write-Host "No report, market-data, calibration, Entry, Exit or Risk file was changed by installer."
Write-Host ""
Write-Host "Next authoritative gate:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host ""
Write-Host "Then run P49.3:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\research_full_first_retest_basin_p493_windows.ps1"
Write-Host ""
Write-Host "Default resumable output:"
Write-Host "  reports\full_first_retest_basin_p493\ALL9_P493_WORKING"
Write-Host "============================================================="
