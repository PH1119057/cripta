$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    throw "EO2 install aborted: $Message"
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

$projectRoot = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "pyproject.toml"))) {
    Fail "run this installer from C:\cripta"
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot "src\bybit_workbench"))) {
    Fail "current directory does not look like C:\cripta"
}
$pyproject = Get-Content -LiteralPath (Join-Path $projectRoot "pyproject.toml") -Raw
if ($pyproject -notmatch 'version\s*=\s*"0\.8\.5"') {
    Fail "baseline mismatch: expected Workbench 0.8.5"
}

$patchRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$payloadRoot = Join-Path $patchRoot "payload"
$hashFile = Join-Path $patchRoot "SHA256SUMS.txt"
$manifestFile = Join-Path $patchRoot "MANIFEST.json"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) { Fail ".venv missing" }
if (-not (Test-Path -LiteralPath $payloadRoot)) { Fail "payload directory missing" }
if (-not (Test-Path -LiteralPath $hashFile)) { Fail "SHA256SUMS.txt missing" }
if (-not (Test-Path -LiteralPath $manifestFile)) { Fail "MANIFEST.json missing" }

$baselineHashes = @{
    "src\bybit_workbench\research\entry_offset_adverse_eo1.py" = "930a098b8f941c8707fedf5bde0515113625ffef3d447115d537eb7aa4ea6272"
    "src\bybit_workbench\research\exit_break_even_v13.py" = "b9e7db22a2256a46ea5b7cb8909816d59a384cad25bf25305754abcfdf5861a2"
    "src\bybit_workbench\research\flow_reversal_v1.py" = "ad8409a7fb8802191e8b05b127e7de83ca76831bcc01ac93b39044eca82b1874"
    "scripts\check_windows.ps1" = "32c708a27040976c2e9085af7c435075e1e43796f04f089978f751390453626c"
    "pyproject.toml" = "36332afc54d76c3f0689f4547c8f89e02dc0142fe257f86ca1c43c8cb8ff869b"
}

$newFiles = @(
    "docs\ENTRY_OFFSET_NO_FLOOR_EO2_PROTOCOL_RU.md",
    "scripts\research_entry_offset_no_floor_eo2_all9_windows.ps1",
    "src\bybit_workbench\research\entry_offset_no_floor_eo2.py",
    "tests\test_entry_offset_no_floor_eo2.py"
)

Write-Host "EO2: verify exact current baseline"
foreach ($relative in $baselineHashes.Keys) {
    $path = Join-Path $projectRoot $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Fail "baseline mismatch; missing: $relative"
    }
    if ((Get-Sha256 $path) -ne $baselineHashes[$relative]) {
        Fail "baseline hash mismatch: $relative"
    }
}
foreach ($relative in $newFiles) {
    if (Test-Path -LiteralPath (Join-Path $projectRoot $relative)) {
        Fail "new EO2 target already exists: $relative"
    }
}

$sourceReport = Join-Path $projectRoot "reports\entry_offset_adverse_eo1\ALL9_EO1_20260822_154907\entry_offset_adverse_events.csv"
if (-not (Test-Path -LiteralPath $sourceReport -PathType Leaf)) {
    Fail "exact completed EO1 source report is missing"
}
$sourceHash = Get-Sha256 $sourceReport
if ($sourceHash -ne "91044aba6f3148e6599a5ce9a7a1414126d19a9cbed28983e19f753203b1d44f") {
    Fail "EO1 source event-table hash mismatch"
}

Write-Host "EO2: verify payload hashes"
$hashRows = @()
foreach ($line in Get-Content -LiteralPath $hashFile) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $parts = $line -split "  ", 2
    if ($parts.Count -ne 2) { Fail "invalid SHA256SUMS line: $line" }
    $expected = $parts[0].Trim().ToLowerInvariant()
    $relative = $parts[1].Trim().Replace("/", "\")
    if ([System.IO.Path]::IsPathRooted($relative)) { Fail "absolute hash path forbidden" }
    $file = Join-Path $payloadRoot $relative
    if (-not (Test-Path -LiteralPath $file)) { Fail "payload missing: $relative" }
    if ((Get-Sha256 $file) -ne $expected) { Fail "payload SHA256 mismatch: $relative" }
    $hashRows += $relative
}
$payloadFiles = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File)
if ($hashRows.Count -ne $payloadFiles.Count) { Fail "payload/hash file-count mismatch" }

$tempRoot = Join-Path $env:TEMP ("cripta-eo2-overlay-" + [guid]::NewGuid().ToString("N"))
$oldPythonPath = $env:PYTHONPATH
$oldMypyPath = $env:MYPYPATH
$oldQt = $env:QT_QPA_PLATFORM
$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION
$backupRoot = $null
$applied = $false

try {
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    Write-Host "EO2: create final-state code overlay"
    foreach ($dir in @("src", "tests", "scripts", "docs")) {
        $sourceDir = Join-Path $projectRoot $dir
        if (Test-Path -LiteralPath $sourceDir) {
            $targetDir = Join-Path $tempRoot $dir
            New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
            & robocopy $sourceDir $targetDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) { Fail "robocopy overlay failed for $dir" }
        }
    }
    foreach ($name in @(
        "pyproject.toml", "uv.lock", ".python-version", ".gitignore", ".env.example",
        "bybit_workbench.spec", "START_WORKBENCH.cmd", "START_MICRO_LIVE.cmd"
    )) {
        $sourceFile = Join-Path $projectRoot $name
        if (Test-Path -LiteralPath $sourceFile) {
            Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $tempRoot $name) -Force
        }
    }

    $reportsSource = Join-Path $projectRoot "reports"
    if (Test-Path -LiteralPath $reportsSource) {
        Write-Host "EO2: snapshot small report fixtures into overlay"
        $reportsTarget = Join-Path $tempRoot "reports"
        New-Item -ItemType Directory -Force -Path $reportsTarget | Out-Null
        & robocopy $reportsSource $reportsTarget `
            "*.csv" "*.json" "*.md" "*.txt" "*.sha256" "*.yml" "*.yaml" `
            /E /XD "dataset" "public_trades" "orderbook_cache" "cache_1m" "__pycache__" `
            /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "robocopy report-fixture snapshot failed" }
    }

    foreach ($file in $payloadFiles) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $tempRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    Write-Host "EO2: PowerShell 5.1 syntax precheck"
    foreach ($script in @(
        (Join-Path $tempRoot "scripts\research_entry_offset_no_floor_eo2_all9_windows.ps1"),
        $MyInvocation.MyCommand.Path
    )) {
        $tokens = $null
        $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile(
            $script,
            [ref]$tokens,
            [ref]$errors
        )
        if ($errors -and $errors.Count -gt 0) {
            $messages = ($errors | ForEach-Object { $_.Message }) -join "; "
            Fail ("PowerShell syntax failed: " + $script + ": " + $messages)
        }
    }

    Push-Location $tempRoot
    try {
        $env:PYTHONPATH = Join-Path $tempRoot "src"
        $env:MYPYPATH = Join-Path $tempRoot "src"

        Write-Host "EO2: py_compile"
        & $python -m py_compile `
            "src\bybit_workbench\research\entry_offset_no_floor_eo2.py" `
            "tests\test_entry_offset_no_floor_eo2.py"
        if ($LASTEXITCODE -ne 0) { Fail "py_compile failed" }

        Write-Host "EO2: Ruff"
        & $python -m ruff check src tests
        if ($LASTEXITCODE -ne 0) { Fail "Ruff failed" }

        Write-Host "EO2: mypy"
        & $python -m mypy src\bybit_workbench
        if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }

        Write-Host "EO2: targeted pytest"
        & $python -m pytest -q `
            tests\test_entry_offset_no_floor_eo2.py `
            tests\test_entry_offset_adverse_eo1.py
        if ($LASTEXITCODE -ne 0) { Fail "targeted pytest failed" }

        Write-Host "EO2: full pytest"
        & $python -m pytest
        if ($LASTEXITCODE -ne 0) { Fail "full pytest failed" }

        Write-Host "EO2: headless smoke"
        $smokeDb = Join-Path $tempRoot "eo2-smoke.db"
        & $python -m bybit_workbench --headless --database $smokeDb
        if ($LASTEXITCODE -ne 0) { Fail "headless smoke failed" }

        Write-Host "EO2: GUI smoke (offscreen, replay/disarmed)"
        $env:QT_QPA_PLATFORM = "offscreen"
        $env:BYBIT_WORKBENCH_PROFILE = "replay"
        $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"
        $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"
        & $python -m bybit_workbench --gui-smoke
        if ($LASTEXITCODE -ne 0) { Fail "GUI smoke failed" }
    }
    finally {
        Pop-Location
    }

    Write-Host "EO2: overlay green; record new-file rollback manifest"
    $backupRoot = Join-Path $patchRoot ("backup_before_apply_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
    New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    @(
        "EO2 is a new-file-only research patch.",
        "No existing project files are overwritten.",
        "On apply/final-gate failure the four EO2 files are deleted.",
        "EO1 source report and reports\ user data are read-only."
    ) | Set-Content -LiteralPath (Join-Path $backupRoot "NEW_FILES_ONLY.txt") -Encoding ASCII

    Write-Host "EO2: apply payload"
    try {
        foreach ($file in $payloadFiles) {
            $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
            $target = Join-Path $projectRoot $relative
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $target
        }
        foreach ($line in Get-Content -LiteralPath $hashFile) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $parts = $line -split "  ", 2
            $expected = $parts[0].Trim().ToLowerInvariant()
            $relative = $parts[1].Trim().Replace("/", "\")
            $target = Join-Path $projectRoot $relative
            if ((Get-Sha256 $target) -ne $expected) {
                throw "post-copy hash mismatch: $relative"
            }
        }
        $applied = $true
    }
    catch {
        Write-Host "EO2: apply failed; removing new EO2 files"
        foreach ($relative in $newFiles) {
            Remove-Item -LiteralPath (Join-Path $projectRoot $relative) -Force -ErrorAction SilentlyContinue
        }
        throw
    }

    Write-Host "EO2: authoritative final Windows gate"
    & powershell -ExecutionPolicy Bypass -File (Join-Path $projectRoot "scripts\check_windows.ps1")
    if ($LASTEXITCODE -ne 0) {
        Write-Host "EO2: authoritative gate red; removing new EO2 files"
        foreach ($relative in $newFiles) {
            Remove-Item -LiteralPath (Join-Path $projectRoot $relative) -Force -ErrorAction SilentlyContinue
        }
        $applied = $false
        Fail "scripts\check_windows.ps1 failed; EO2 new files removed"
    }

    Write-Host "EO2 installed successfully."
    Write-Host ("Rollback manifest: " + $backupRoot)
    Write-Host "Existing Entry/Exit/Risk/Execution/live files were not modified."
    Write-Host "Run research:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_offset_no_floor_eo2_all9_windows.ps1"
}
finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:MYPYPATH = $oldMypyPath
    $env:QT_QPA_PLATFORM = $oldQt
    $env:BYBIT_WORKBENCH_PROFILE = $oldProfile
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = $oldAllowLive
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = $oldTestnet
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
