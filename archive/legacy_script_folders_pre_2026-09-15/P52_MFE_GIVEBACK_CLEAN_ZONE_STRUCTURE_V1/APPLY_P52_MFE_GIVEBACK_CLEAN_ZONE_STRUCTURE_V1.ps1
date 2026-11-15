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
Write-Host "P52 MFE + GIVEBACK + CLEAN ZONE STRUCTURE V1 - FAIL-CLOSED"
Write-Host "Project: $ProjectRoot"
Write-Host "Baseline: bybit-workbench 0.8.5 + accepted P45.1/P50/P51 source"
Write-Host "Research only. Downloads: DISABLED. NEW5: NOT TOUCHED."
Write-Host "Adds causal support/resistance correlation and fixed stop trade-off."
Write-Host "No optimizer. No live Entry / Exit / Risk / Execution changes."
Write-Host "============================================================="

if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml") -PathType Leaf)) {
    throw "pyproject.toml not found under $ProjectRoot"
}
if (-not (Test-Path $Python -PathType Leaf)) { throw "Python venv not found: $Python" }
if (-not (Test-Path $ManifestPath -PathType Leaf)) { throw "Manifest missing: $ManifestPath" }
if (-not (Test-Path $BaselinePath -PathType Leaf)) { throw "Baseline missing: $BaselinePath" }

$CurrentInit = Join-Path $ProjectRoot "src\bybit_workbench\__init__.py"
if (-not (Test-Path $CurrentInit -PathType Leaf)) { throw "Package baseline missing" }
$InitText = Get-Content $CurrentInit -Raw
if (-not $InitText.Contains('__version__ = "0.8.5"')) {
    throw "Unexpected project version. P52 requires bybit-workbench 0.8.5."
}

$BaselineRows = @(Import-Csv $BaselinePath)
foreach ($Row in $BaselineRows) {
    $Relative = ([string]$Row.Path).Replace("/", "\")
    if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative.Contains("..")) {
        throw "Unsafe baseline path: $Relative"
    }
    $Target = Join-Path $ProjectRoot $Relative
    if (-not (Test-Path $Target -PathType Leaf)) { throw "Required baseline missing: $Relative" }
    $Actual = (Get-FileHash -Algorithm SHA256 $Target).Hash.ToLowerInvariant()
    $Expected = ([string]$Row.SHA256).Trim().ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "Baseline hash mismatch: $Relative. P52 must be rebuilt for current source."
    }
}
Write-Host "Baseline hashes: OK"

$Rows = @(Import-Csv $ManifestPath)
if ($Rows.Count -eq 0) { throw "Manifest is empty" }
foreach ($Row in $Rows) {
    $Relative = [string]$Row.Path
    if ([string]::IsNullOrWhiteSpace($Relative) -or $Relative.Contains("..")) {
        throw "Unsafe payload path: $Relative"
    }
    if ($Relative.StartsWith("reports/") -or $Relative.StartsWith("data/") -or $Relative.StartsWith("var/")) {
        throw "Forbidden payload path: $Relative"
    }
    $Source = Join-Path $PayloadRoot ($Relative.Replace("/", "\"))
    if (-not (Test-Path $Source -PathType Leaf)) { throw "Payload missing: $Relative" }
    $Actual = (Get-FileHash -Algorithm SHA256 $Source).Hash.ToLowerInvariant()
    $Expected = ([string]$Row.SHA256).Trim().ToLowerInvariant()
    if ($Actual -ne $Expected) { throw "Payload hash mismatch: $Relative" }
}
Write-Host "Payload hashes: OK"

$PowerShellFiles = @(Get-ChildItem $PayloadRoot -Recurse -File -Filter "*.ps1")
foreach ($File in $PowerShellFiles) {
    $Tokens = $null
    $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $File.FullName, [ref]$Tokens, [ref]$Errors
    ) | Out-Null
    if ($Errors.Count -gt 0) { throw "PowerShell syntax failed: $($Errors[0].Message)" }
}
Write-Host "PowerShell payload syntax: OK"

$Stage = Join-Path ([System.IO.Path]::GetTempPath()) (
    "p52_structure_overlay_" + [Guid]::NewGuid().ToString("N")
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
        $Target = Join-Path $Stage $Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
        Copy-Item $Source $Target -Force
    }

    & $Python -m compileall -q (Join-Path $Stage "src") (Join-Path $Stage "tests")
    if ($LASTEXITCODE -ne 0) { throw "Python compileall overlay failed" }
    Write-Host "Python compileall overlay: OK"

    Push-Location $Stage
    try {
        & $Python -m ruff check --config (Join-Path $ProjectRoot "pyproject.toml") src tests
        if ($LASTEXITCODE -ne 0) { throw "Ruff overlay precheck failed" }
        Write-Host "Ruff overlay precheck: OK"

        $env:MYPYPATH = (Join-Path $Stage "src")
        & $Python -m mypy --config-file (Join-Path $ProjectRoot "pyproject.toml") `
            (Join-Path $Stage "src\bybit_workbench")
        if ($LASTEXITCODE -ne 0) { throw "mypy overlay precheck failed" }
        Write-Host "mypy overlay precheck: OK"

        $env:PYTHONPATH = (Join-Path $Stage "src")
        & $Python -m pytest -q -c (Join-Path $ProjectRoot "pyproject.toml") `
            "tests\test_mfe_giveback_clean_zone_p52.py" `
            "tests\test_mfe_activated_risk_p51.py" `
            "tests\test_clean_zone_lifecycle_p451.py" `
            "tests\test_multi_retest_entry_recross_p50.py" `
            "tests\test_first_retest_stop_anatomy_p49.py"
        if ($LASTEXITCODE -ne 0) { throw "Targeted pytest overlay precheck failed" }
        Write-Host "Targeted pytest overlay precheck: OK"
    }
    finally { Pop-Location }
}
finally {
    $env:MYPYPATH = $PreviousMypyPath
    $env:PYTHONPATH = $PreviousPythonPath
    if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue }
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupRoot = Join-Path $ProjectRoot "patch_backups\P52_STRUCTURE_V1_$Timestamp"
$BackedUp = 0
foreach ($Row in $Rows) {
    $Relative = ([string]$Row.Path).Replace("/", "\")
    $Target = Join-Path $ProjectRoot $Relative
    if (Test-Path $Target -PathType Leaf) {
        $Backup = Join-Path $BackupRoot $Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $Backup) -Force | Out-Null
        Copy-Item $Target $Backup -Force
        $BackedUp += 1
    }
}
foreach ($Row in $Rows) {
    $Relative = ([string]$Row.Path).Replace("/", "\")
    $Source = Join-Path $PayloadRoot $Relative
    $Target = Join-Path $ProjectRoot $Relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $Target) -Force | Out-Null
    Copy-Item $Source $Target -Force
    Write-Host "Applied: $Relative"
}

Write-Host "============================================================="
Write-Host "P52 PATCH APPLIED"
if ($BackedUp -gt 0) { Write-Host "Backup: $BackupRoot ($BackedUp existing files)" }
Write-Host "Package version remains bybit-workbench 0.8.5."
Write-Host "No reports, market data, Entry, Exit, Risk or Execution files changed."
Write-Host ""
Write-Host "Authoritative gate:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host ""
Write-Host "Then run P52:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\research_mfe_giveback_clean_zone_p52_windows.ps1"
Write-Host "============================================================="
