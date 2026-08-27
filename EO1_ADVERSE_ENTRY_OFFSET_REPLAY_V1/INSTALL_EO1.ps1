$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    throw "EO1 install aborted: $Message"
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
foreach ($relative in @(
    "scripts\check_windows.ps1",
    "bybit_workbench.spec",
    "src\bybit_workbench\research\exit_break_even_v13.py",
    "src\bybit_workbench\research\flow_reversal_v1.py"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relative))) {
        Fail "baseline mismatch; missing: $relative"
    }
}

$targets = @(
    "src\bybit_workbench\research\entry_offset_adverse_eo1.py",
    "tests\test_entry_offset_adverse_eo1.py",
    "scripts\research_entry_offset_adverse_eo1_all9_windows.ps1",
    "docs\ENTRY_OFFSET_ADVERSE_EO1_PROTOCOL_RU.md"
)
foreach ($relative in $targets) {
    if (Test-Path -LiteralPath (Join-Path $projectRoot $relative)) {
        Fail "target already exists; additive patch refuses overwrite: $relative"
    }
}

Write-Host "EO1: verify payload hashes"
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
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { Fail "payload SHA256 mismatch: $relative" }
    $hashRows += $relative
}
$payloadFiles = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File)
if ($hashRows.Count -ne $payloadFiles.Count) { Fail "payload/hash file-count mismatch" }

$tempRoot = Join-Path $env:TEMP ("cripta-eo1-overlay-" + [guid]::NewGuid().ToString("N"))
$oldPythonPath = $env:PYTHONPATH
$oldMypyPath = $env:MYPYPATH
$oldQt = $env:QT_QPA_PLATFORM
$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION

try {
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    Write-Host "EO1: create final-state code overlay"
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
        Write-Host "EO1: snapshot small report fixtures into overlay"
        $reportsTarget = Join-Path $tempRoot "reports"
        New-Item -ItemType Directory -Force -Path $reportsTarget | Out-Null
        & robocopy $reportsSource $reportsTarget `
            "*.csv" "*.json" "*.md" "*.txt" "*.sha256" "*.yml" "*.yaml" `
            /E /XD "dataset" "public_trades" "orderbook_cache" "__pycache__" `
            /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { Fail "robocopy report-fixture snapshot failed" }
    }

    $all9 = @(
        "UNIUSDT", "LINKUSDT", "BTCUSDT", "ETHUSDT", "XRPUSDT",
        "1000PEPEUSDT", "SOLUSDT", "DOGEUSDT", "ADAUSDT"
    )
    foreach ($symbol in $all9) {
        $fixtureRelative = Join-Path `
            ("reports\cross_asset_validation\" + $symbol + "_20260518_20260816\p40") `
            "absorption_features.csv"
        if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $fixtureRelative))) {
            Fail "baseline full-test fixture missing: $fixtureRelative"
        }
        if (-not (Test-Path -LiteralPath (Join-Path $tempRoot $fixtureRelative))) {
            Fail "overlay full-test fixture missing after snapshot: $fixtureRelative"
        }
    }

    foreach ($file in $payloadFiles) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $tempRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    Write-Host "EO1: PowerShell 5.1 syntax precheck"
    $tokens = $null
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $tempRoot "scripts\research_entry_offset_adverse_eo1_all9_windows.ps1"),
        [ref]$tokens,
        [ref]$errors
    )
    if ($errors -and $errors.Count -gt 0) {
        $messages = ($errors | ForEach-Object { $_.Message }) -join "; "
        Fail ("PowerShell syntax failed: " + $messages)
    }

    Push-Location $tempRoot
    try {
        $env:PYTHONPATH = Join-Path $tempRoot "src"
        $env:MYPYPATH = Join-Path $tempRoot "src"

        Write-Host "EO1: py_compile"
        & $python -m py_compile `
            "src\bybit_workbench\research\entry_offset_adverse_eo1.py" `
            "tests\test_entry_offset_adverse_eo1.py"
        if ($LASTEXITCODE -ne 0) { Fail "py_compile failed" }

        Write-Host "EO1: Ruff"
        & $python -m ruff check src tests
        if ($LASTEXITCODE -ne 0) { Fail "Ruff failed" }

        Write-Host "EO1: mypy"
        & $python -m mypy src\bybit_workbench
        if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }

        Write-Host "EO1: targeted pytest"
        & $python -m pytest -q tests\test_entry_offset_adverse_eo1.py
        if ($LASTEXITCODE -ne 0) { Fail "targeted pytest failed" }

        Write-Host "EO1: full pytest"
        & $python -m pytest
        if ($LASTEXITCODE -ne 0) { Fail "full pytest failed" }

        Write-Host "EO1: headless smoke"
        $smokeDb = Join-Path $tempRoot "eo1-smoke.db"
        & $python -m bybit_workbench --headless --database $smokeDb
        if ($LASTEXITCODE -ne 0) { Fail "headless smoke failed" }

        Write-Host "EO1: GUI smoke (offscreen, replay/disarmed)"
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

    Write-Host "EO1: overlay green; apply additive files"
    foreach ($file in $payloadFiles) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $projectRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    Write-Host "EO1 installed successfully."
    Write-Host "Additive research only; existing project/report/user-data files were not changed."
    Write-Host "Entry, P50/P51, SE1/SE2, Exit, Risk, Execution, MAYAK and live are unchanged."
    Write-Host "Run authoritative final gate now:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
    Write-Host "Then run research:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\research_entry_offset_adverse_eo1_all9_windows.ps1"
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
