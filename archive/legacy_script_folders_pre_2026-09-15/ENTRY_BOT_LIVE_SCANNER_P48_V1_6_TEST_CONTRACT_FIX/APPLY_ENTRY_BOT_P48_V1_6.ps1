param(
    [string]$ProjectRoot = "C:\cripta"
)

$ErrorActionPreference = "Stop"
$PatchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PayloadRoot = Join-Path $PatchRoot "payload"
$ManifestPath = Join-Path $PatchRoot "MANIFEST.csv"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Write-Host "============================================================="
Write-Host "ENTRY BOT P48 V1.6 TEST CONTRACT FIX - PRECHECK"
Write-Host "Project: $ProjectRoot"
Write-Host "BOT MODE no longer auto-starts screening."
Write-Host "Uncalibrated assets are skipped by REST warm-up."
Write-Host "Transient REST TLS/read timeouts are retried."
Write-Host "One failed asset no longer kills the other ready assets."
Write-Host "Auto Mainnet Entry remains LOCKED."
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

$PowerShellFiles = @(Get-ChildItem $PayloadRoot -Recurse -File -Filter "*.ps1")
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

$PythonFiles = @(Get-ChildItem $PayloadRoot -Recurse -File -Filter "*.py")
foreach ($File in $PythonFiles) {
    & $Python -m py_compile $File.FullName
    if ($LASTEXITCODE -ne 0) {
        throw "Python syntax failed: $($File.FullName)"
    }
}
Write-Host "Python syntax: OK"

$Stage = Join-Path ([System.IO.Path]::GetTempPath()) (
    "entry_bot_p48_v16_overlay_" + [Guid]::NewGuid().ToString("N")
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
            "tests\test_entry_bot_live_scanner.py" "tests\test_config.py" `
            "tests\test_gui_smoke.py"
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
$BackupRoot = Join-Path $ProjectRoot "patch_backups\ENTRY_BOT_P48_V1_6_$Timestamp"
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
Write-Host ""
Write-Host "Next:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host ""
Write-Host "Build PARTIAL calibration now (ready old assets only):"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\build_entry_bot_calibration_windows.ps1"
Write-Host ""
Write-Host "After all ten P35 results exist:"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\build_entry_bot_calibration_windows.ps1 -RequireAll"
Write-Host ""
Write-Host "BOT MODE only opens the panel. Press 'Start screening' explicitly."
Write-Host "Auto Mainnet Entry remains LOCKED."
Write-Host "============================================================="
