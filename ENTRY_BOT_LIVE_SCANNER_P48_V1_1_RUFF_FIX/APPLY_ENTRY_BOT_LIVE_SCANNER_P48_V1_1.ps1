param(
    [string]$ProjectRoot = "C:\cripta"
)

$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $PatchRoot "payload"
$ManifestPath = Join-Path $PatchRoot "MANIFEST.csv"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "============================================================="
Write-Host "ENTRY BOT LIVE SCANNER P48 V1.1 RUFF FIX - PRECHECK"
Write-Host "Project: $ProjectRoot"
Write-Host "10 working assets; BTC/ETH reference-only."
Write-Host "No market data will be downloaded by installer."
Write-Host "Auto Mainnet Entry remains LOCKED in this patch."
Write-Host "P46 / Exit / Risk logic will NOT be modified."
Write-Host "============================================================="

if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml"))) {
    throw "pyproject.toml not found under $ProjectRoot"
}
if (-not (Test-Path $Python)) {
    throw "Python venv not found: $Python"
}
if (-not (Test-Path $ManifestPath)) {
    throw "Manifest not found: $ManifestPath"
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

$PowerShellFiles = @(
    Get-ChildItem $PayloadRoot -Recurse -File -Filter "*.ps1"
)
foreach ($File in $PowerShellFiles) {
    $Tokens = $null
    $Errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $File.FullName,
        [ref]$Tokens,
        [ref]$Errors
    )
    if ($Errors.Count -gt 0) {
        throw "PowerShell syntax failed: $($File.FullName): $($Errors[0].Message)"
    }
}
Write-Host "PowerShell syntax: OK"

$PythonFiles = @(
    Get-ChildItem $PayloadRoot -Recurse -File -Filter "*.py"
)
foreach ($File in $PythonFiles) {
    & $Python -m py_compile $File.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "Python syntax failed: $($File.FullName)"
    }
}
Write-Host "Python syntax: OK"

$PythonPaths = @($PythonFiles | ForEach-Object { $_.FullName })
& $Python -m ruff check @PythonPaths
if ($LASTEXITCODE -ne 0) {
    throw "Ruff payload precheck failed; project files were not modified."
}
Write-Host "Ruff payload precheck: OK"

$Stage = Join-Path ([System.IO.Path]::GetTempPath()) (
    "entry_bot_p48_mypy_" + [Guid]::NewGuid().ToString("N")
)
$PreviousMypyPath = $env:MYPYPATH
try {
    $StageSrc = Join-Path $Stage "src"
    New-Item -ItemType Directory -Path $StageSrc -Force | Out-Null
    Copy-Item (Join-Path $ProjectRoot "src\bybit_workbench") $StageSrc -Recurse -Force

    foreach ($Row in $Rows) {
        $Relative = ([string]$Row.Path).Replace("/", "\")
        if (-not $Relative.StartsWith("src\bybit_workbench\")) {
            continue
        }
        $Source = Join-Path $PayloadRoot $Relative
        $StageTarget = Join-Path $Stage $Relative
        $StageParent = Split-Path -Parent $StageTarget
        New-Item -ItemType Directory -Path $StageParent -Force | Out-Null
        Copy-Item $Source $StageTarget -Force
    }

    $env:MYPYPATH = $StageSrc
    Push-Location $Stage
    try {
        & $Python -m mypy --config-file (Join-Path $ProjectRoot "pyproject.toml") -p bybit_workbench
        if ($LASTEXITCODE -ne 0) {
            throw "mypy source-overlay precheck failed; project files were not modified."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:MYPYPATH = $PreviousMypyPath
    if (Test-Path $Stage) {
        Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
Write-Host "mypy source-overlay precheck: OK"

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$BackupRoot = Join-Path $ProjectRoot "patch_backups\ENTRY_BOT_LIVE_SCANNER_P48_V1_1_$Timestamp"
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
else {
    Write-Host "Backup: not needed; all project targets were new files"
}
Write-Host ""
Write-Host "Next commands:"
Write-Host "  cd C:\cripta"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host ""
Write-Host "After P35 completes for all ten working assets:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\build_entry_bot_calibration_windows.ps1 -RequireAll"
Write-Host ""
Write-Host "Then start Workbench and enable: BOT MODE · 10 монет"
Write-Host "Auto Mainnet Entry is intentionally still LOCKED."
Write-Host "============================================================="
