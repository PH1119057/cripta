$ErrorActionPreference = "Stop"

function Fail([string]$Message) { throw "EO3.1 install aborted: $Message" }
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
    "src\bybit_workbench\research\entry_offset_adverse_eo1.py" = "930a098b8f941c8707fedf5bde0515113625ffef3d447115d537eb7aa4ea6272"
    "src\bybit_workbench\research\exit_break_even_v13.py" = "b9e7db22a2256a46ea5b7cb8909816d59a384cad25bf25305754abcfdf5861a2"
    "src\bybit_workbench\research\flow_reversal_v1.py" = "ad8409a7fb8802191e8b05b127e7de83ca76831bcc01ac93b39044eca82b1874"
    "src\bybit_workbench\research\entry_offset_no_floor_eo2.py" = "53422fb92f5dad81536592890b137b7aad97e426e61cd5ea48d41107c9032ed2"
    "tests\test_entry_offset_no_floor_eo2.py" = "c7d0c48061eff6b25b64963347c5ba6f20f2661fd1c552c4c4c8ead225506da7"
}
$newFiles = @(
    "docs\ENTRY_FULL_PATH_ANATOMY_EO3_PROTOCOL_RU.md",
    "scripts\research_entry_full_path_anatomy_eo3_all9_windows.ps1",
    "src\bybit_workbench\research\entry_full_path_anatomy_eo3.py",
    "tests\test_entry_full_path_anatomy_eo3.py"
)

Write-Host "EO3.1: verify exact EO1.2 + EO2.3 baseline"
foreach ($relative in $baselineHashes.Keys) {
    $path = Join-Path $projectRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Fail "baseline missing: $relative" }
    if ((Get-Sha256 $path) -ne $baselineHashes[$relative]) { Fail "baseline hash mismatch: $relative" }
}
foreach ($relative in $newFiles) {
    if (Test-Path -LiteralPath (Join-Path $projectRoot $relative)) { Fail "EO3 target already exists: $relative" }
}
$sourceReport = Join-Path $projectRoot "reports\entry_offset_adverse_eo1\ALL9_EO1_20260822_154907\entry_offset_adverse_events.csv"
if (-not (Test-Path -LiteralPath $sourceReport -PathType Leaf)) { Fail "exact EO1 source report missing" }
if ((Get-Sha256 $sourceReport) -ne "91044aba6f3148e6599a5ce9a7a1414126d19a9cbed28983e19f753203b1d44f") { Fail "EO1 source event hash mismatch" }

Write-Host "EO3.1: verify payload hashes"
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

$tempRoot = Join-Path $env:TEMP ("cripta-eo3-overlay-" + [guid]::NewGuid().ToString("N"))
$oldPythonPath = $env:PYTHONPATH
$oldMypyPath = $env:MYPYPATH
$oldQt = $env:QT_QPA_PLATFORM
$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION
$applied = $false
try {
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    Write-Host "EO3.1: create final-state code overlay"
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
        Write-Host "EO3.1: snapshot small report fixtures into overlay"
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

    Write-Host "EO3.1: PowerShell 5.1 syntax precheck"
    foreach ($script in @((Join-Path $tempRoot "scripts\research_entry_full_path_anatomy_eo3_all9_windows.ps1"), $MyInvocation.MyCommand.Path)) {
        $tokens = $null; $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errors)
        if ($errors -and $errors.Count -gt 0) { Fail (($errors | ForEach-Object { $_.Message }) -join "; ") }
    }

    Push-Location $tempRoot
    try {
        $env:PYTHONPATH = Join-Path $tempRoot "src"
        $env:MYPYPATH = Join-Path $tempRoot "src"
        Write-Host "EO3.1: py_compile"
        & $python -m py_compile "src\bybit_workbench\research\entry_full_path_anatomy_eo3.py" "tests\test_entry_full_path_anatomy_eo3.py"
        if ($LASTEXITCODE -ne 0) { Fail "py_compile failed" }
        Write-Host "EO3.1: Ruff"
        & $python -m ruff check src tests
        if ($LASTEXITCODE -ne 0) { Fail "Ruff failed" }
        Write-Host "EO3.1: mypy"
        & $python -m mypy src\bybit_workbench
        if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }
        Write-Host "EO3.1: targeted pytest"
        & $python -m pytest -q tests\test_entry_full_path_anatomy_eo3.py tests\test_entry_offset_no_floor_eo2.py tests\test_entry_offset_adverse_eo1.py
        if ($LASTEXITCODE -ne 0) { Fail "targeted pytest failed" }
        Write-Host "EO3.1: full pytest"
        & $python -m pytest
        if ($LASTEXITCODE -ne 0) { Fail "full pytest failed" }
        Write-Host "EO3.1: headless smoke"
        & $python -m bybit_workbench --headless --database (Join-Path $tempRoot "eo3-smoke.db")
        if ($LASTEXITCODE -ne 0) { Fail "headless smoke failed" }
        Write-Host "EO3.1: GUI smoke (offscreen, replay/disarmed)"
        $env:QT_QPA_PLATFORM = "offscreen"; $env:BYBIT_WORKBENCH_PROFILE = "replay"; $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"; $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"
        & $python -m bybit_workbench --gui-smoke
        if ($LASTEXITCODE -ne 0) { Fail "GUI smoke failed" }
    }
    finally { Pop-Location }

    Write-Host "EO3.1: overlay green; apply new research files"
    try {
        foreach ($file in $payloadFiles) {
            $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
            $target = Join-Path $projectRoot $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $target
        }
        $applied = $true
    }
    catch {
        foreach ($relative in $newFiles) { Remove-Item -LiteralPath (Join-Path $projectRoot $relative) -Force -ErrorAction SilentlyContinue }
        throw
    }
    Write-Host "EO3.1: authoritative final Windows gate"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $projectRoot "scripts\check_windows.ps1")
    if ($LASTEXITCODE -ne 0) {
        foreach ($relative in $newFiles) { Remove-Item -LiteralPath (Join-Path $projectRoot $relative) -Force -ErrorAction SilentlyContinue }
        $applied = $false
        Fail "check_windows.ps1 failed; EO3 files removed"
    }
    Write-Host "EO3.1 installed successfully."
    Write-Host "Research-only; existing Entry/Exit/Risk/Execution/live files were not modified."
    Write-Host "Run: powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_full_path_anatomy_eo3_all9_windows.ps1"
}
finally {
    $env:PYTHONPATH = $oldPythonPath; $env:MYPYPATH = $oldMypyPath; $env:QT_QPA_PLATFORM = $oldQt; $env:BYBIT_WORKBENCH_PROFILE = $oldProfile; $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = $oldAllowLive; $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = $oldTestnet
    if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }
}
