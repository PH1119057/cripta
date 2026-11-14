$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    throw "SE2 install aborted: $Message"
}

function Assert-FileSha256([string]$Path, [string]$Expected, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Fail "$Label missing: $Path"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        Fail "$Label SHA256 mismatch. Expected $Expected, got $actual"
    }
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

if (-not (Test-Path -LiteralPath $python)) {
    Fail ".venv missing; run scripts\setup_windows.ps1 first"
}
if (-not (Test-Path -LiteralPath $payloadRoot)) { Fail "payload directory missing" }
if (-not (Test-Path -LiteralPath $hashFile)) { Fail "SHA256SUMS.txt missing" }
if (-not (Test-Path -LiteralPath $manifestFile)) { Fail "MANIFEST.json missing" }

$se1Module = Join-Path $projectRoot "src\bybit_workbench\research\secondary_entry_se1.py"
$se1Test = Join-Path $projectRoot "tests\test_secondary_entry_se1.py"
$se1Runner = Join-Path $projectRoot "scripts\research_secondary_entry_se1_all9_windows.ps1"
Assert-FileSha256 $se1Module `
    "d136ea02e5a72fe26d48da865b526985611d07ca0383c8b5c1384c1e8087ba92" `
    "accepted SE1.3 module"
Assert-FileSha256 $se1Test `
    "181dd95eb9feb34a68e9de454952749ffa1a6f8a757b32471130dd32a23075af" `
    "accepted SE1.3 tests"
Assert-FileSha256 $se1Runner `
    "03de129d6084246c403aa5a90e3047165616788f54ba4b75cb8a6309150e13ef" `
    "accepted SE1.3 runner"

$se1Events = Join-Path `
    $projectRoot `
    "reports\secondary_entry_se1\ALL9_SE1_WORKING\secondary_entry_events.csv"
$se1Contract = Join-Path `
    $projectRoot `
    "reports\secondary_entry_se1\ALL9_SE1_WORKING\run_contract.json"
Assert-FileSha256 $se1Events `
    "1dca79fdaa452c346d5ff5249d3fb028a8ce33e5788fa6e1e53c89215cf41424" `
    "frozen SE1 event machine truth"
if (-not (Test-Path -LiteralPath $se1Contract)) {
    Fail "SE1 run contract missing: $se1Contract"
}
$se1ContractJson = Get-Content -LiteralPath $se1Contract -Raw | ConvertFrom-Json
if ($se1ContractJson.contract_sha256 -ne `
    "2d198b2220adae9cd3f2a997b481e90d4bf85722d8afac009ceecff63a4e82cc") {
    Fail "SE1 run contract SHA mismatch"
}

$requiredBaseline = @(
    "scripts\check_windows.ps1",
    "bybit_workbench.spec"
)
foreach ($relative in $requiredBaseline) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relative))) {
        Fail "baseline mismatch; missing: $relative"
    }
}

$targets = @(
    "src\bybit_workbench\research\secondary_entry_se2.py",
    "tests\test_secondary_entry_se2.py",
    "scripts\research_secondary_entry_se2_all9_windows.ps1",
    "docs\SECONDARY_ENTRY_SE2_CLEAN_LAUNCH_PROTOCOL_RU.md"
)
foreach ($relative in $targets) {
    if (Test-Path -LiteralPath (Join-Path $projectRoot $relative)) {
        Fail "target already exists; additive patch refuses overwrite: $relative"
    }
}

Write-Host "SE2: verify payload hashes"
$hashRows = @()
foreach ($line in Get-Content -LiteralPath $hashFile) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    $parts = $line -split "  ", 2
    if ($parts.Count -ne 2) { Fail "invalid SHA256SUMS line: $line" }
    $expected = $parts[0].Trim().ToLowerInvariant()
    $relative = $parts[1].Trim().Replace("/", "\")
    if ([System.IO.Path]::IsPathRooted($relative)) {
        Fail "absolute hash path forbidden: $relative"
    }
    $file = Join-Path $payloadRoot $relative
    if (-not (Test-Path -LiteralPath $file)) { Fail "payload missing: $relative" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { Fail "payload SHA256 mismatch: $relative" }
    $hashRows += $relative
}
$payloadFiles = @(Get-ChildItem -LiteralPath $payloadRoot -Recurse -File)
if ($hashRows.Count -ne $payloadFiles.Count) {
    Fail "payload/hash file-count mismatch"
}

$tempRoot = Join-Path $env:TEMP ("cripta-se2-overlay-" + [guid]::NewGuid().ToString("N"))
$oldPythonPath = $env:PYTHONPATH
$oldMypyPath = $env:MYPYPATH
$oldQt = $env:QT_QPA_PLATFORM
$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION

try {
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    Write-Host "SE2: create final-state code overlay"

    $projectDirs = @("src", "tests", "scripts", "docs")
    foreach ($dir in $projectDirs) {
        $sourceDir = Join-Path $projectRoot $dir
        if (Test-Path -LiteralPath $sourceDir) {
            $targetDir = Join-Path $tempRoot $dir
            New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
            & robocopy $sourceDir $targetDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
            if ($LASTEXITCODE -ge 8) {
                Fail "robocopy overlay failed for $dir with code $LASTEXITCODE"
            }
        }
    }

    $projectFiles = @(
        "pyproject.toml",
        "uv.lock",
        ".python-version",
        ".gitignore",
        ".env.example",
        "bybit_workbench.spec",
        "START_WORKBENCH.cmd",
        "START_MICRO_LIVE.cmd"
    )
    foreach ($name in $projectFiles) {
        $sourceFile = Join-Path $projectRoot $name
        if (Test-Path -LiteralPath $sourceFile) {
            Copy-Item -LiteralPath $sourceFile -Destination (Join-Path $tempRoot $name) -Force
        }
    }

    # Existing full-suite research tests depend on small machine-truth fixtures in reports.
    # Copy a private snapshot into the overlay. Never junction the real reports directory.
    $reportsSource = Join-Path $projectRoot "reports"
    if (Test-Path -LiteralPath $reportsSource) {
        Write-Host "SE2: snapshot small report fixtures into overlay"
        $reportsTarget = Join-Path $tempRoot "reports"
        New-Item -ItemType Directory -Force -Path $reportsTarget | Out-Null
        & robocopy $reportsSource $reportsTarget `
            "*.csv" "*.json" "*.md" "*.txt" "*.sha256" "*.yml" "*.yaml" `
            /E /XD "dataset" "public_trades" "orderbook_cache" "__pycache__" `
            /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Fail "robocopy report-fixture snapshot failed with code $LASTEXITCODE"
        }
    }

    $all9 = @(
        "UNIUSDT",
        "LINKUSDT",
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "1000PEPEUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "ADAUSDT"
    )
    foreach ($symbol in $all9) {
        $fixtureRelative = Join-Path `
            ("reports\cross_asset_validation\" + $symbol + "_20260518_20260816\p40") `
            "absorption_features.csv"
        $sourceFixture = Join-Path $projectRoot $fixtureRelative
        $overlayFixture = Join-Path $tempRoot $fixtureRelative
        if (-not (Test-Path -LiteralPath $sourceFixture)) {
            Fail "baseline full-test fixture missing: $fixtureRelative"
        }
        if (-not (Test-Path -LiteralPath $overlayFixture)) {
            Fail "overlay full-test fixture missing after snapshot: $fixtureRelative"
        }
    }

    foreach ($file in $payloadFiles) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $tempRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    if (-not (Test-Path -LiteralPath (Join-Path $tempRoot "bybit_workbench.spec"))) {
        Fail "overlay incomplete: bybit_workbench.spec was not copied"
    }

    Write-Host "SE2: PowerShell 5.1 syntax precheck"
    $tokens = $null
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $tempRoot "scripts\research_secondary_entry_se2_all9_windows.ps1"),
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

        Write-Host "SE2: py_compile"
        & $python -m py_compile `
            "src\bybit_workbench\research\secondary_entry_se2.py" `
            "tests\test_secondary_entry_se2.py"
        if ($LASTEXITCODE -ne 0) { Fail "py_compile failed" }

        Write-Host "SE2: Ruff"
        & $python -m ruff check src tests
        if ($LASTEXITCODE -ne 0) { Fail "Ruff failed" }

        Write-Host "SE2: mypy"
        & $python -m mypy src\bybit_workbench
        if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }

        Write-Host "SE2: targeted pytest"
        & $python -m pytest -q tests\test_secondary_entry_se2.py
        if ($LASTEXITCODE -ne 0) { Fail "targeted pytest failed" }

        Write-Host "SE2: full pytest"
        & $python -m pytest
        if ($LASTEXITCODE -ne 0) { Fail "full pytest failed" }

        Write-Host "SE2: headless smoke"
        $smokeDb = Join-Path $tempRoot "se2-smoke.db"
        & $python -m bybit_workbench --headless --database $smokeDb
        if ($LASTEXITCODE -ne 0) { Fail "headless smoke failed" }

        Write-Host "SE2: GUI smoke (offscreen, replay/disarmed)"
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

    Write-Host "SE2: overlay green; apply additive files"
    foreach ($file in $payloadFiles) {
        $relative = $file.FullName.Substring($payloadRoot.Length).TrimStart("\")
        $target = Join-Path $projectRoot $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    Write-Host "SE2 installed successfully."
    Write-Host "Additive research only; no existing project/report/user-data file was changed."
    Write-Host "Entry, P50/P51, Main -1 stop, Exit, Risk, Execution, MAYAK and live are unchanged."
    Write-Host "Run authoritative final gate now:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
    Write-Host "Then run discovery:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\research_secondary_entry_se2_all9_windows.ps1"
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
