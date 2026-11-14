param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$PatchName = "P53_1M_ENTRY_DISPLACEMENT_V1"
$ProjectRoot = (Get-Location).Path
$PatchRoot = $PSScriptRoot
$ManifestPath = Join-Path $PatchRoot "MANIFEST.json"
$PayloadRoot = Join-Path $PatchRoot "payload"
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("Cripta_P53_" + [guid]::NewGuid().ToString("N"))
$OverlayRoot = Join-Path $TempRoot "overlay"
$TempBackup = Join-Path $TempRoot "backup"
$Applied = New-Object System.Collections.Generic.List[string]
$Existing = @{}

function Fail([string]$Message) {
    throw $Message
}

function Get-RelPath([string]$Root, [string]$FullPath) {
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\\') + '\\'
    $pathFull = [System.IO.Path]::GetFullPath($FullPath)
    if (-not $pathFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "Path escapes root: $FullPath"
    }
    return $pathFull.Substring($rootFull.Length)
}

function Assert-AsciiPs1([string]$Path) {
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    foreach ($b in $bytes) {
        if ($b -gt 127) { Fail "Executable PS1 must be ASCII for Windows PowerShell 5.1: $Path" }
    }
}

function Assert-PowerShellSyntax([string]$Path) {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
    if ($errors -and $errors.Count -gt 0) {
        $message = ($errors | ForEach-Object { $_.Message }) -join "; "
        Fail "PowerShell syntax error in $Path : $message"
    }
}

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    Write-Host "[CHECK] $Label"
    & $Command
    if ($LASTEXITCODE -ne 0) { Fail "$Label failed with exit code $LASTEXITCODE" }
}

function Copy-OverlayItem([string]$Name) {
    $source = Join-Path $ProjectRoot $Name
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination $OverlayRoot -Recurse -Force
    }
}

function Restore-RealProject() {
    Write-Host "[ROLLBACK] Restoring real project..."
    foreach ($rel in $Applied) {
        $target = Join-Path $ProjectRoot $rel
        if ($Existing.ContainsKey($rel) -and $Existing[$rel]) {
            $backup = Join-Path $TempBackup $rel
            $parent = Split-Path -Parent $target
            if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            Copy-Item -LiteralPath $backup -Destination $target -Force
        }
        else {
            if (Test-Path $target -PathType Leaf) { Remove-Item -LiteralPath $target -Force }
        }
    }
}

try {
    Write-Host "============================================================="
    Write-Host "$PatchName - FAIL-CLOSED INSTALLER"
    Write-Host "Project: $ProjectRoot"
    Write-Host "Baseline: bybit-workbench 0.8.5 / P48.2 current source snapshot"
    Write-Host "Research only. Downloads: DISABLED."
    Write-Host "Entry / Exit / Risk / Execution / live / UI: UNCHANGED."
    Write-Host "Reports and user data are not touched by installation."
    Write-Host "============================================================="

    if (-not (Test-Path $ManifestPath -PathType Leaf)) { Fail "Manifest missing: $ManifestPath" }
    if (-not (Test-Path $PayloadRoot -PathType Container)) { Fail "Payload missing: $PayloadRoot" }
    foreach ($required in @("src", "tests", "scripts", "docs", "patches", "pyproject.toml")) {
        if (-not (Test-Path (Join-Path $ProjectRoot $required))) {
            Fail "Run from current project root C:\\cripta; missing: $required"
        }
    }
    $expectedRoot = [System.IO.Path]::GetFullPath("C:\\cripta").TrimEnd('\\')
    if ([System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\\') -ne $expectedRoot) {
        Fail "Installer must be run from C:\\cripta. Current directory: $ProjectRoot"
    }

    $Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $Python -PathType Leaf)) { Fail "Project venv Python missing: $Python" }
    $PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json

    Write-Host "[1/7] Verify baseline hashes"
    foreach ($property in $manifest.baseline.required_file_sha256.PSObject.Properties) {
        $rel = [string]$property.Name
        $expected = ([string]$property.Value).ToLowerInvariant()
        $path = Join-Path $ProjectRoot $rel
        if (-not (Test-Path $path -PathType Leaf)) { Fail "Baseline file missing: $rel" }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { Fail "Baseline hash mismatch: $rel expected=$expected actual=$actual" }
    }

    Write-Host "[2/7] Verify payload hashes"
    foreach ($property in $manifest.payload_sha256.PSObject.Properties) {
        $rel = [string]$property.Name
        $expected = ([string]$property.Value).ToLowerInvariant()
        $path = Join-Path $PayloadRoot $rel
        if (-not (Test-Path $path -PathType Leaf)) { Fail "Payload file missing: $rel" }
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { Fail "Payload hash mismatch: $rel expected=$expected actual=$actual" }
    }

    Write-Host "[3/7] Build temp overlay"
    New-Item -ItemType Directory -Path $OverlayRoot -Force | Out-Null
    foreach ($name in @("src", "tests", "scripts", "docs", "patches", "pyproject.toml", "uv.lock")) {
        Copy-OverlayItem $name
    }
    foreach ($property in $manifest.payload_sha256.PSObject.Properties) {
        $rel = [string]$property.Name
        $source = Join-Path $PayloadRoot $rel
        $target = Join-Path $OverlayRoot $rel
        $parent = Split-Path -Parent $target
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $source -Destination $target -Force
    }

    Write-Host "[4/7] PowerShell 5.1 syntax / ASCII precheck"
    $researchPs1 = Join-Path $OverlayRoot "scripts\research_entry_1m_displacement_p53_windows.ps1"
    Assert-AsciiPs1 $researchPs1
    Assert-AsciiPs1 $PSCommandPath
    Assert-PowerShellSyntax $researchPs1
    Assert-PowerShellSyntax $PSCommandPath

    Write-Host "[5/7] Strong overlay gate"
    Push-Location $OverlayRoot
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = Join-Path $OverlayRoot "src"
        Invoke-Checked "py_compile P53" { & $Python -m py_compile "src\bybit_workbench\research\entry_one_minute_displacement_p53.py" "tests\test_entry_one_minute_displacement_p53.py" }
        Invoke-Checked "Ruff src tests" { & $Python -m ruff check src tests }
        Invoke-Checked "mypy bybit_workbench" { & $Python -m mypy "src\bybit_workbench" }
        Invoke-Checked "targeted P53 pytest" { & $Python -m pytest "tests\test_entry_one_minute_displacement_p53.py" }
        Invoke-Checked "full pytest" { & $Python -m pytest }
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
        Pop-Location
    }

    Write-Host "[6/7] Apply payload to real project after green overlay"
    New-Item -ItemType Directory -Path $TempBackup -Force | Out-Null
    foreach ($property in $manifest.payload_sha256.PSObject.Properties) {
        $rel = [string]$property.Name
        $source = Join-Path $PayloadRoot $rel
        $target = Join-Path $ProjectRoot $rel
        $exists = Test-Path $target -PathType Leaf
        $Existing[$rel] = $exists
        if ($exists) {
            $backup = Join-Path $TempBackup $rel
            $backupParent = Split-Path -Parent $backup
            if (-not (Test-Path $backupParent)) { New-Item -ItemType Directory -Path $backupParent -Force | Out-Null }
            Copy-Item -LiteralPath $target -Destination $backup -Force
        }
        $parent = Split-Path -Parent $target
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $source -Destination $target -Force
        $Applied.Add($rel)
    }

    Write-Host "[7/7] Authoritative real-project Windows gate"
    Push-Location $ProjectRoot
    try {
        & $PowerShell -ExecutionPolicy Bypass -File ".\scripts\check_windows.ps1"
        if ($LASTEXITCODE -ne 0) { Fail "Authoritative scripts\\check_windows.ps1 failed with exit code $LASTEXITCODE" }
    }
    finally { Pop-Location }

    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupRoot = Join-Path $ProjectRoot ("patch_backups\\" + $PatchName + "_" + $stamp)
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    $backupLog = New-Object System.Collections.Generic.List[string]
    foreach ($rel in $Applied) {
        if ($Existing[$rel]) {
            $source = Join-Path $TempBackup $rel
            $target = Join-Path $backupRoot $rel
            $parent = Split-Path -Parent $target
            if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
            Copy-Item -LiteralPath $source -Destination $target -Force
            $backupLog.Add("BACKED_UP  $rel")
        }
        else {
            $backupLog.Add("ADDED_NEW  $rel")
        }
    }
    $backupLog | Set-Content -LiteralPath (Join-Path $backupRoot "BACKUP_LOG.txt") -Encoding ASCII
    Copy-Item -LiteralPath $ManifestPath -Destination (Join-Path $backupRoot "MANIFEST.json") -Force

    Write-Host "============================================================="
    Write-Host "INSTALLED: $PatchName"
    Write-Host "Authoritative Windows gate: GREEN"
    Write-Host "Resulting product version remains 0.8.5 / P48.2 (research overlay only)."
    Write-Host "Next research command:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_1m_displacement_p53_windows.ps1"
    Write-Host "============================================================="
}
catch {
    $message = $_.Exception.Message
    if ($Applied.Count -gt 0) {
        try { Restore-RealProject } catch { Write-Host "ROLLBACK ERROR: $($_.Exception.Message)" -ForegroundColor Red }
    }
    Write-Host "============================================================="
    Write-Host "NOT INSTALLED: $PatchName" -ForegroundColor Red
    Write-Host "CAUSE: $message" -ForegroundColor Red
    Write-Host "Real project was not intentionally left modified; reports/user data were not touched." -ForegroundColor Red
    Write-Host "============================================================="
    exit 1
}
finally {
    if (Test-Path $TempRoot) { Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
