param(
    [string]$ProjectRoot = "C:\cripta"
)

$ErrorActionPreference = "Stop"
$patchRoot = $PSScriptRoot
$payloadRoot = Join-Path $patchRoot "payload"
$manifestPath = Join-Path $patchRoot "MANIFEST.csv"

if (-not (Test-Path $ProjectRoot)) {
    throw "Project root not found: $ProjectRoot"
}
if (-not (Test-Path (Join-Path $ProjectRoot "pyproject.toml"))) {
    throw "pyproject.toml not found under project root: $ProjectRoot"
}
if (-not (Test-Path $payloadRoot)) {
    throw "Patch payload not found: $payloadRoot"
}
if (-not (Test-Path $manifestPath)) {
    throw "Patch manifest not found: $manifestPath"
}

$manifest = @(Import-Csv -Path $manifestPath)
if ($manifest.Count -eq 0) {
    throw "Patch manifest is empty."
}

Write-Host "============================================================="
Write-Host "P31 RUNTIME V4 PATCH - PRECHECK"
Write-Host "Project: $ProjectRoot"
Write-Host "Patch files: $($manifest.Count)"
Write-Host "reports\ will NOT be modified."
Write-Host "Live trading logic will NOT be modified."
Write-Host "============================================================="

foreach ($item in $manifest) {
    $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    $source = Join-Path $payloadRoot $relative
    if (-not (Test-Path $source)) {
        throw "Payload file missing: $source"
    }
    $actualHash = (Get-FileHash -Path $source -Algorithm SHA256).Hash.ToLowerInvariant()
    $expectedHash = ([string]$item.sha256).ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "SHA256 mismatch for payload file: $relative"
    }
}
Write-Host "Payload hashes: OK"

$powerShellFiles = @($manifest | Where-Object { ([string]$_.relative_path).EndsWith(".ps1") })
foreach ($item in $powerShellFiles) {
    $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    $source = Join-Path $payloadRoot $relative
    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $source,
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null
    if ($parseErrors.Count -gt 0) {
        $messages = ($parseErrors | ForEach-Object { $_.Message }) -join "; "
        throw "PowerShell syntax error in $relative : $messages"
    }
}
Write-Host "PowerShell syntax: OK"

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $python) {
    $pythonFiles = @($manifest | Where-Object { ([string]$_.relative_path).EndsWith(".py") })
    foreach ($item in $pythonFiles) {
        $relative = ([string]$item.relative_path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
        $source = Join-Path $payloadRoot $relative
        & $python -m py_compile $source
        if ($LASTEXITCODE -ne 0) {
            throw "Python syntax check failed: $relative"
        }
    }
    Get-ChildItem -Path $payloadRoot -Directory -Recurse -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Python syntax: OK"
}
else {
    Write-Warning "Project .venv Python not found; Python syntax precheck skipped: $python"
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupRoot = Join-Path $ProjectRoot "patch_backups\ENTRY_P31_RUNTIME_V4_$stamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

$state = @()
try {
    foreach ($item in $manifest) {
        $relativeSlash = [string]$item.relative_path
        $relative = $relativeSlash.Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
        if ($relative -like "reports\*" -or $relative -eq "reports") {
            throw "Safety violation: patch attempted to touch reports: $relative"
        }

        $source = Join-Path $payloadRoot $relative
        $destination = Join-Path $ProjectRoot $relative
        $existed = Test-Path $destination
        $backup = Join-Path $backupRoot $relative

        if ($existed) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup) | Out-Null
            Copy-Item -Path $destination -Destination $backup -Force
        }

        $state += [pscustomobject]@{
            relative = $relative
            destination = $destination
            backup = $backup
            existed = $existed
        }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -Path $source -Destination $destination -Force
        Write-Host "Applied: $relative"
    }
}
catch {
    Write-Error "Patch apply failed. Rolling back files already copied..."
    for ($index = $state.Count - 1; $index -ge 0; $index--) {
        $entry = $state[$index]
        if ($entry.existed -and (Test-Path $entry.backup)) {
            Copy-Item -Path $entry.backup -Destination $entry.destination -Force
        }
        elseif (-not $entry.existed -and (Test-Path $entry.destination)) {
            Remove-Item -Path $entry.destination -Force
        }
    }
    throw
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
Write-Host "Run without -AllowDownload. Completed UNI/LINK/P30 BTC outputs are reused."
Write-Host "============================================================="
