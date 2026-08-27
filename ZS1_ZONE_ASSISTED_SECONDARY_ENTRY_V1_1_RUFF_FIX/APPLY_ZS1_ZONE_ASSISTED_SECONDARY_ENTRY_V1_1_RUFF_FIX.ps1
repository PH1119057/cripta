$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$Root = (Resolve-Path ".").Path
$PackageRoot = $PSScriptRoot
$PayloadRoot = Join-Path $PackageRoot "payload"
$ManifestPath = Join-Path $PackageRoot "MANIFEST.json"
$BackupRoot = $null
$Overlay = $null
$OldPythonPath = $env:PYTHONPATH

function Fail([string]$Message) {
    throw $Message
}

function Ensure-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Fail "$Label missing: $Path"
    }
}

function File-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Copy-Tree([string]$Source, [string]$Destination) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        Fail "Source directory missing: $Source"
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
}

try {
    Write-Host "============================================================="
    Write-Host "ZS1 V1.1 RUFF FIX - FAIL-CLOSED INSTALLER"
    Write-Host "Project root: $Root"
    Write-Host "Baseline: bybit-workbench 0.8.5 + frozen P52/SE1/SE2 reports"
    Write-Host "Research logic: unchanged from ZS1 V1."
    Write-Host "Research only. Downloads: DISABLED. NEW5: NOT ACCESSED."
    Write-Host "Entry / Exit / Risk / Execution / live: NOT CHANGED."
    Write-Host "============================================================="

    if ((Split-Path -Leaf $Root) -ne "cripta") {
        Fail "Run this installer from C:\cripta"
    }

    $PyProject = Join-Path $Root "pyproject.toml"
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    $Ruff = Join-Path $Root ".venv\Scripts\ruff.exe"
    $Mypy = Join-Path $Root ".venv\Scripts\mypy.exe"
    Ensure-File $PyProject "pyproject.toml"
    Ensure-File $Python "Python venv"
    Ensure-File $Ruff "Ruff"
    Ensure-File $Mypy "mypy"
    Ensure-File $ManifestPath "MANIFEST"

    $PyProjectText = Get-Content -LiteralPath $PyProject -Raw
    if ($PyProjectText -notmatch '(?m)^version\s*=\s*"0\.8\.5"\s*$') {
        Fail "Baseline version mismatch: expected pyproject version 0.8.5"
    }

    Write-Host "ZS1 V1.1: verify frozen report prerequisites"
    $ExpectedSources = @{
        "reports\mfe_giveback_clean_zone_p52\ALL9_P52_WORKING\signal_first_structure_full.csv" = "5f7c577b2f2bfd673973d63ac42d872af395e7116f3ac7d91bae11cca371f7ee"
        "reports\mfe_giveback_clean_zone_p52\ALL9_P52_WORKING\signal_first_structure_60m.csv" = "439454235857415ec2b564819e3c5792181e3793ebf8b0ddecbd169fedd79530"
        "reports\mfe_giveback_clean_zone_p52\ALL9_P52_WORKING\summary.json" = "1bc3707403a7ddf712ae69e1e9698e5f2ac1222650113a5d2d5bed3f43b3b586"
        "reports\secondary_entry_se1\ALL9_SE1_WORKING\secondary_entry_events.csv" = "1dca79fdaa452c346d5ff5249d3fb028a8ce33e5788fa6e1e53c89215cf41424"
        "reports\secondary_entry_se1\ALL9_SE1_WORKING\summary.json" = "a441ef257958229738c27926eb09811f25f967cb3387bd1000f0795354ae9452"
        "reports\secondary_entry_se1\ALL9_SE1_WORKING\run_contract.json" = "2ee76fd5512f1bd4b7a90aad2339391718532b069f81910fc995e31566950a3d"
        "reports\secondary_entry_se2\ALL9_SE2_DISCOVERY_20260821_161847\selected_candidates.csv" = "f062663f9636c789e9f5820683346fcd5939ed5a8e053991e46dbeecff9dcb4d"
        "reports\secondary_entry_se2\ALL9_SE2_DISCOVERY_20260821_161847\selected_candidate_events.csv" = "b551ead0b22c11ed5892a328ccd599d8f2d0e328f1450dbb03d9b853b22ea884"
        "reports\secondary_entry_se2\ALL9_SE2_DISCOVERY_20260821_161847\summary.json" = "b1b25a7a2b64ae74ca32b0647f778fcbe9d6d24b42f4ebcec511a54937b97729"
        "reports\secondary_entry_se2\ALL9_SE2_DISCOVERY_20260821_161847\provenance.json" = "09a0dc10b2a60b7d95ba6579a0ea82ae19d7e1614cf0e14b2119dfbed0ef3919"
    }
    foreach ($Relative in $ExpectedSources.Keys) {
        $Path = Join-Path $Root $Relative
        Ensure-File $Path "Frozen research source"
        $Actual = File-Sha256 $Path
        if ($Actual -ne $ExpectedSources[$Relative]) {
            Fail "Frozen report hash mismatch: $Relative expected=$($ExpectedSources[$Relative]) actual=$Actual"
        }
    }
    Write-Host "Frozen report hashes: OK"

    Write-Host "ZS1 V1.1: verify payload hashes"
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    foreach ($Item in $Manifest.payload_files) {
        $PayloadPath = Join-Path $PayloadRoot ([string]$Item.path)
        Ensure-File $PayloadPath "Payload file"
        $Actual = File-Sha256 $PayloadPath
        if ($Actual -ne ([string]$Item.sha256).ToLowerInvariant()) {
            Fail "Payload hash mismatch: $($Item.path)"
        }
    }
    Write-Host "Payload hashes: OK"

    Write-Host "ZS1 V1.1: create final-state overlay"
    $Overlay = Join-Path $env:TEMP ("zs1_v11_overlay_" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $Overlay | Out-Null
    Copy-Item -LiteralPath $PyProject -Destination (Join-Path $Overlay "pyproject.toml") -Force
    Copy-Tree (Join-Path $Root "src") (Join-Path $Overlay "src")
    Copy-Tree (Join-Path $Root "tests") (Join-Path $Overlay "tests")
    Copy-Tree (Join-Path $Root "scripts") (Join-Path $Overlay "scripts")

    foreach ($Item in $Manifest.payload_files) {
        $Relative = ([string]$Item.path).Replace("/", "\")
        $Source = Join-Path $PayloadRoot $Relative
        $Destination = Join-Path $Overlay $Relative
        $Parent = Split-Path -Parent $Destination
        New-Item -ItemType Directory -Path $Parent -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
    }

    Write-Host "ZS1 V1.1: PowerShell 5.1 syntax precheck"
    Get-ChildItem -LiteralPath $PayloadRoot -Recurse -File -Filter *.ps1 | ForEach-Object {
        $Text = Get-Content -LiteralPath $_.FullName -Raw
        [void][ScriptBlock]::Create($Text)
    }
    Write-Host "PowerShell payload syntax: OK"

    Write-Host "ZS1 V1.1: py_compile changed Python"
    & $Python -m py_compile `
        (Join-Path $Overlay "src\bybit_workbench\research\secondary_entry_zone_scale_zs1.py") `
        (Join-Path $Overlay "tests\test_secondary_entry_zone_scale_zs1.py")
    if ($LASTEXITCODE -ne 0) {
        Fail "py_compile overlay precheck failed"
    }

    Push-Location $Overlay
    try {
        Write-Host "ZS1 V1.1: Ruff final overlay src + tests"
        & $Ruff check src tests
        if ($LASTEXITCODE -ne 0) {
            Fail "Ruff src/tests overlay precheck failed"
        }

        Write-Host "ZS1 V1.1: mypy full overlay source"
        & $Mypy src
        if ($LASTEXITCODE -ne 0) {
            Fail "mypy overlay precheck failed"
        }

        Write-Host "ZS1 V1.1: targeted pytest"
        $env:PYTHONPATH = (Join-Path $Overlay "src")
        & $Python -m pytest -q tests\test_secondary_entry_zone_scale_zs1.py
        if ($LASTEXITCODE -ne 0) {
            Fail "targeted pytest overlay precheck failed"
        }
    }
    finally {
        Pop-Location
        $env:PYTHONPATH = $OldPythonPath
    }

    Write-Host "ZS1 V1.1: overlay precheck PASSED; project may now be changed"

    $Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $BackupRoot = Join-Path $Root ("patch_backups\ZS1_V1_1_RUFF_FIX_" + $Stamp)
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $BackupLines = New-Object System.Collections.Generic.List[string]

    foreach ($Item in $Manifest.payload_files) {
        $Relative = ([string]$Item.path).Replace("/", "\")
        $Source = Join-Path $PayloadRoot $Relative
        $Destination = Join-Path $Root $Relative
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            $BackupPath = Join-Path $BackupRoot $Relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $BackupPath) -Force | Out-Null
            Copy-Item -LiteralPath $Destination -Destination $BackupPath -Force
            $BackupLines.Add("BACKUP $Relative")
        }
        else {
            $BackupLines.Add("NEW $Relative")
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
    }
    $BackupLines | Set-Content -LiteralPath (Join-Path $BackupRoot "BACKUP_LOG.txt") -Encoding ASCII

    Write-Host "ZS1 V1.1: install complete"
    Write-Host "Backup: $BackupRoot"
    Write-Host "Application version remains 0.8.5"
    Write-Host "Research logic remains ZS1_ZONE_ASSISTED_SECONDARY_ENTRY_V1"
    Write-Host ""
    Write-Host "NEXT REQUIRED GATE:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\check_windows.ps1"
    Write-Host ""
    Write-Host "THEN RUN RESEARCH:"
    Write-Host "powershell -ExecutionPolicy Bypass -File .\scripts\research_secondary_entry_zone_scale_zs1_windows.ps1"
}
catch {
    Write-Error $_
    Write-Host "ZS1 V1.1 INSTALL FAILED."
    Write-Host "If failure occurred before 'overlay precheck PASSED', project files and reports were not touched."
    exit 1
}
finally {
    $env:PYTHONPATH = $OldPythonPath
    if ($Overlay -and (Test-Path -LiteralPath $Overlay)) {
        Remove-Item -LiteralPath $Overlay -Recurse -Force -ErrorAction SilentlyContinue
    }
}
