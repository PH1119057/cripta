$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$patchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$payloadRoot = Join-Path $patchRoot "payload"
$manifestPath = Join-Path $patchRoot "MANIFEST.csv"
$projectRoot = "C:\cripta"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

Write-Host "============================================================="
Write-Host "P46 CONFIRMATORY HOLDOUT V1.1 PS SYNTAX FIX - PRECHECK"
Write-Host "Project: $projectRoot"
Write-Host "reports\ will NOT be modified by installer."
Write-Host "No market data will be downloaded by installer."
Write-Host "Live trading / Exit / Risk logic will NOT be modified."
Write-Host "============================================================="

if (-not (Test-Path $projectRoot)) { throw "Project root not found: $projectRoot" }
if (-not (Test-Path $python)) { throw "Python environment not found: $python" }
if (-not (Test-Path $manifestPath)) { throw "Manifest not found: $manifestPath" }

$manifest = Import-Csv -Path $manifestPath
Write-Host "Patch files: $($manifest.Count)"
foreach ($item in $manifest) {
    $source = Join-Path $payloadRoot $item.path
    if (-not (Test-Path $source)) { throw "Payload file missing: $source" }
    $actual = (Get-FileHash -Algorithm SHA256 -Path $source).Hash.ToLowerInvariant()
    if ($actual -ne $item.sha256.ToLowerInvariant()) {
        throw "Payload hash mismatch: $($item.path)"
    }
}
Write-Host "Payload hashes: OK"

$psFiles = @(
    Get-ChildItem -Path (Join-Path $payloadRoot "scripts") -Filter "*.ps1" -File
)
foreach ($file in $psFiles) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName,
        [ref]$tokens,
        [ref]$errors
    ) | Out-Null
    if ($errors.Count -gt 0) {
        throw "PowerShell syntax failed: $($file.FullName): $($errors[0].Message)"
    }
}
Write-Host "PowerShell syntax: OK"

$pyFiles = @(
    Get-ChildItem -Path $payloadRoot -Filter "*.py" -File -Recurse
)
foreach ($file in $pyFiles) {
    & $python -m py_compile $file.FullName
    if ($LASTEXITCODE -ne 0) { throw "Python syntax failed: $($file.FullName)" }
}
Write-Host "Python syntax: OK"

$payloadSrc = Join-Path $payloadRoot "src\bybit_workbench\research\confirmatory_holdout_p46.py"
$payloadTest = Join-Path $payloadRoot "tests\test_confirmatory_holdout_p46.py"
Push-Location $projectRoot
try {
    & $python -m ruff check $payloadSrc $payloadTest
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff payload precheck failed; project files were not modified."
    }
}
finally {
    Pop-Location
}
Write-Host "Ruff payload precheck: OK"

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $projectRoot "patch_backups\P46_CONFIRMATORY_HOLDOUT_V1_1_$stamp"
$backedUp = $false
foreach ($item in $manifest) {
    $target = Join-Path $projectRoot $item.path
    if (Test-Path $target) {
        $backup = Join-Path $backupRoot $item.path
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
        Copy-Item -Path $target -Destination $backup -Force
        $backedUp = $true
    }
}

foreach ($item in $manifest) {
    $source = Join-Path $payloadRoot $item.path
    $target = Join-Path $projectRoot $item.path
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -Path $source -Destination $target -Force
    Write-Host "Applied: $($item.path)"
}

Write-Host "============================================================="
Write-Host "PATCH APPLIED"
if ($backedUp) { Write-Host "Backup: $backupRoot" }
else { Write-Host "Backup: not needed; all project targets were new files" }
Write-Host ""
Write-Host "Next commands:"
Write-Host "  cd C:\cripta"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\freeze_p46_confirmatory_holdout_windows.ps1"
Write-Host ""
Write-Host "Do NOT prepare or evaluate the holdout before 2026-09-18 00:00 UTC."
Write-Host "The code intentionally blocks partial-holdout peeking."
Write-Host "============================================================="
