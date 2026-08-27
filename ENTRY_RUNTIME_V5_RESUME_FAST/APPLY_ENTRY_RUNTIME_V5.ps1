$ErrorActionPreference = "Stop"

$patchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $patchRoot
$payloadRoot = Join-Path $patchRoot "payload"
$manifestPath = Join-Path $patchRoot "MANIFEST.csv"

if (-not (Test-Path $manifestPath)) {
    throw "Manifest not found: $manifestPath"
}
if (-not (Test-Path (Join-Path $projectRoot "pyproject.toml"))) {
    throw "Project root not detected: $projectRoot"
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

$manifest = @(Import-Csv -Path $manifestPath)
if ($manifest.Count -eq 0) {
    throw "Manifest is empty."
}

Write-Host "============================================================="
Write-Host "ENTRY RUNTIME V5 PATCH - PRECHECK"
Write-Host "Project: $projectRoot"
Write-Host "Patch files: $($manifest.Count)"
Write-Host "reports\ will NOT be modified."
Write-Host "Live trading logic will NOT be modified."
Write-Host "============================================================="

foreach ($row in $manifest) {
    $relative = [string]$row.relative_path
    if ($relative -like "reports\*" -or $relative -like "reports/*") {
        throw "Forbidden reports path in manifest: $relative"
    }
    $source = Join-Path $payloadRoot $relative
    if (-not (Test-Path $source)) {
        throw "Payload file missing: $relative"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -Path $source).Hash.ToLowerInvariant()
    $expected = ([string]$row.sha256).ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "Payload hash mismatch: $relative"
    }
}
Write-Host "Payload hashes: OK"

$parseFailed = $false
foreach ($row in $manifest) {
    $relative = [string]$row.relative_path
    if (-not $relative.EndsWith(".ps1", [StringComparison]::OrdinalIgnoreCase)) {
        continue
    }
    $source = Join-Path $payloadRoot $relative
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $source,
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors.Count -gt 0) {
        foreach ($errorItem in $errors) {
            Write-Host ("PowerShell syntax error in {0}: {1}" -f $relative, $errorItem.Message)
        }
        $parseFailed = $true
    }
}
if ($parseFailed) {
    throw "PowerShell syntax check failed."
}
Write-Host "PowerShell syntax: OK"

foreach ($row in $manifest) {
    $relative = [string]$row.relative_path
    if (-not $relative.EndsWith(".py", [StringComparison]::OrdinalIgnoreCase)) {
        continue
    }
    $source = Join-Path $payloadRoot $relative
    & $python -m py_compile $source
    if ($LASTEXITCODE -ne 0) {
        throw "Python syntax check failed: $relative"
    }
}
Write-Host "Python syntax: OK"

$stamp = [DateTime]::Now.ToString("yyyyMMdd_HHmmss")
$backupRoot = Join-Path $projectRoot "patch_backups\ENTRY_RUNTIME_V5_$stamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

foreach ($row in $manifest) {
    $relative = [string]$row.relative_path
    $source = Join-Path $payloadRoot $relative
    $destination = Join-Path $projectRoot $relative
    if (Test-Path $destination) {
        $backup = Join-Path $backupRoot $relative
        $backupParent = Split-Path -Parent $backup
        New-Item -ItemType Directory -Force -Path $backupParent | Out-Null
        Copy-Item -Path $destination -Destination $backup -Force
    }
    $destinationParent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    Copy-Item -Path $source -Destination $destination -Force
    Write-Host "Applied: $relative"
}

Write-Host "============================================================="
Write-Host "PATCH APPLIED"
Write-Host "Backup: $backupRoot"
Write-Host ""
Write-Host "Next commands:"
Write-Host "  cd C:\cripta"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_full_panel_windows.ps1"
Write-Host ""
Write-Host "Run without -AllowDownload. Completed stages will be resumed/reused."
Write-Host "============================================================="
