$ErrorActionPreference = "Stop"

function Fail([string]$Message) { throw "GUI connection race fix install aborted: $Message" }
function Get-Sha256([string]$Path) { return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }

$projectRoot = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "pyproject.toml"))) { Fail "run from C:\cripta" }
$pyproject = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw
if ($pyproject -notmatch 'version\s*=\s*"0\.8\.5"') { Fail "expected Workbench 0.8.5" }
$patchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$payloadRoot = Join-Path $patchRoot "payload"
$hashFile = Join-Path $patchRoot "SHA256SUMS.txt"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { Fail ".venv missing" }

$baselineHashes = @{
    "pyproject.toml" = "36332afc54d76c3f0689f4547c8f89e02dc0142fe257f86ca1c43c8cb8ff869b"
    "scripts\check_windows.ps1" = "32c708a27040976c2e9085af7c435075e1e43796f04f089978f751390453626c"
    "src\bybit_workbench\ui\main_window.py" = "4dc74e7cbb9939febc3e5666fe963be485e4510b32aae79df3f1eac0354a5215"
    "tests\test_gui_smoke.py" = "d437a1bcd04d212e911d0f0899e142e7adc79d41e0377299fb9b8bc7d9f042e9"
}
$changedFiles = @(
    "src\bybit_workbench\ui\main_window.py",
    "tests\test_gui_smoke.py"
)

Write-Host "GUI race fix: verify exact current baseline"
foreach ($relative in $baselineHashes.Keys) {
    $path = Join-Path $projectRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Fail "baseline missing: $relative" }
    if ((Get-Sha256 $path) -ne $baselineHashes[$relative]) { Fail "baseline hash mismatch: $relative" }
}

Write-Host "GUI race fix: verify payload hashes"
$hashRows = @()
foreach ($line in Get-Content -LiteralPath $hashFile) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $parts = $line -split "  ", 2
    if ($parts.Count -ne 2) { Fail "invalid SHA256SUMS line" }
    $expected = $parts[0].Trim().ToLowerInvariant()
    $relative = $parts[1].Trim().Replace("/", "\")
    $file = Join-Path $payloadRoot $relative
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { Fail "payload missing: $relative" }
    if ((Get-Sha256 $file) -ne $expected) { Fail "payload hash mismatch: $relative" }
    $hashRows += $relative
}
$payloadFiles = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File)
if ($hashRows.Count -ne $payloadFiles.Count) { Fail "payload/hash file-count mismatch" }

$tempRoot = Join-Path $env:TEMP ("cripta-gui-race-overlay-" + [guid]::NewGuid().ToString("N"))
$backupRoot = Join-Path $patchRoot ("backup_before_apply_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
$oldPythonPath = $env:PYTHONPATH
$oldMypyPath = $env:MYPYPATH
$oldQt = $env:QT_QPA_PLATFORM
$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION
$applied = $false
try {
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    Write-Host "GUI race fix: create final-state code overlay"
    foreach ($dir in @("src", "tests", "scripts", "docs")) {
        $src = Join-Path $projectRoot $dir
        if (Test-Path -LiteralPath $src) {
            $dst = Join-Path $tempRoot $dir
            New-Item -ItemType Directory -Force -Path $dst | Out-Null
            & robocopy $src $dst /E /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { Fail "robocopy failed: $dir" }
        }
    }
    foreach ($name in @("pyproject.toml", "uv.lock", ".python-version", ".gitignore", ".env.example", "bybit_workbench.spec")) {
        $src = Join-Path $projectRoot $name
        if (Test-Path -LiteralPath $src) { Copy-Item -LiteralPath $src -Destination (Join-Path $tempRoot $name) -Force }
    }
    $reportsSource = Join-Path $projectRoot "reports"
    if (Test-Path -LiteralPath $reportsSource) {
        Write-Host "GUI race fix: snapshot small report fixtures into overlay"
        $reportsTarget = Join-Path $tempRoot "reports"
        New-Item -ItemType Directory -Force -Path $reportsTarget | Out-Null
        & robocopy $reportsSource $reportsTarget "*.csv" "*.json" "*.md" "*.txt" "*.sha256" /E /XD "dataset" "public_trades" "orderbook_cache" "cache_1m" "__pycache__" /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "report-fixture snapshot failed" }
    }
    foreach ($file in $payloadFiles) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $tempRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    Write-Host "GUI race fix: PowerShell 5.1 syntax precheck"
    $tokens = $null; $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($MyInvocation.MyCommand.Path, [ref]$tokens, [ref]$errors)
    if ($errors -and $errors.Count -gt 0) { Fail (($errors | ForEach-Object { $_.Message }) -join "; ") }

    Push-Location $tempRoot
    try {
        $env:PYTHONPATH = Join-Path $tempRoot "src"
        $env:MYPYPATH = Join-Path $tempRoot "src"
        Write-Host "GUI race fix: py_compile"
        & $python -m py_compile "src\bybit_workbench\ui\main_window.py" "tests\test_gui_smoke.py"
        if ($LASTEXITCODE -ne 0) { Fail "py_compile failed" }
        Write-Host "GUI race fix: Ruff"
        & $python -m ruff check src tests
        if ($LASTEXITCODE -ne 0) { Fail "Ruff failed" }
        Write-Host "GUI race fix: mypy"
        & $python -m mypy src\bybit_workbench
        if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }
        Write-Host "GUI race fix: targeted GUI pytest"
        & $python -m pytest -q tests\test_gui_smoke.py
        if ($LASTEXITCODE -ne 0) { Fail "targeted GUI pytest failed" }
        Write-Host "GUI race fix: full pytest"
        & $python -m pytest
        if ($LASTEXITCODE -ne 0) { Fail "full pytest failed" }
        Write-Host "GUI race fix: headless smoke"
        & $python -m bybit_workbench --headless --database (Join-Path $tempRoot "gui-race-smoke.db")
        if ($LASTEXITCODE -ne 0) { Fail "headless smoke failed" }
        Write-Host "GUI race fix: GUI smoke (offscreen, replay/disarmed)"
        $env:QT_QPA_PLATFORM = "offscreen"; $env:BYBIT_WORKBENCH_PROFILE = "replay"; $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"; $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"
        & $python -m bybit_workbench --gui-smoke
        if ($LASTEXITCODE -ne 0) { Fail "GUI smoke failed" }
    }
    finally { Pop-Location }

    Write-Host "GUI race fix: overlay green; create backup"
    foreach ($relative in $changedFiles) {
        $src = Join-Path $projectRoot $relative
        $dst = Join-Path $backupRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
        Copy-Item -LiteralPath $src -Destination $dst -Force
    }

    Write-Host "GUI race fix: apply payload"
    try {
        foreach ($file in $payloadFiles) {
            $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
            $target = Join-Path $projectRoot $relative
            Copy-Item -LiteralPath $file.FullName -Destination $target -Force
        }
        foreach ($relative in $hashRows) {
            $payloadFile = Join-Path $payloadRoot $relative
            $target = Join-Path $projectRoot $relative
            if ((Get-Sha256 $target) -ne (Get-Sha256 $payloadFile)) { Fail "post-copy hash mismatch: $relative" }
        }
        $applied = $true
    }
    catch {
        foreach ($relative in $changedFiles) {
            $backup = Join-Path $backupRoot $relative
            if (Test-Path -LiteralPath $backup) { Copy-Item -LiteralPath $backup -Destination (Join-Path $projectRoot $relative) -Force }
        }
        throw
    }

    Write-Host "GUI race fix: authoritative final Windows gate"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $projectRoot "scripts\check_windows.ps1")
    if ($LASTEXITCODE -ne 0) {
        foreach ($relative in $changedFiles) {
            $backup = Join-Path $backupRoot $relative
            Copy-Item -LiteralPath $backup -Destination (Join-Path $projectRoot $relative) -Force
        }
        $applied = $false
        Fail "check_windows.ps1 failed; original UI/test restored"
    }
    Write-Host "GUI connection activity race fix installed successfully."
    Write-Host "Backup: $backupRoot"
    Write-Host "Does not change Entry, Exit, Risk, Execution, research logic, reports or user data."
}
finally {
    $env:PYTHONPATH = $oldPythonPath; $env:MYPYPATH = $oldMypyPath; $env:QT_QPA_PLATFORM = $oldQt; $env:BYBIT_WORKBENCH_PROFILE = $oldProfile; $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = $oldAllowLive; $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = $oldTestnet
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
