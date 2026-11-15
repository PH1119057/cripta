$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$root = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml"))) {
    throw "SE2: run from C:\cripta."
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "SE2: .venv missing; run scripts\setup_windows.ps1 first."
}

$se1Dir = Join-Path $root "reports\secondary_entry_se1\ALL9_SE1_WORKING"
$se1Events = Join-Path $se1Dir "secondary_entry_events.csv"
$se1Contract = Join-Path $se1Dir "run_contract.json"
if (-not (Test-Path -LiteralPath $se1Events)) {
    throw "SE2: SE1 event table missing: $se1Events"
}
if (-not (Test-Path -LiteralPath $se1Contract)) {
    throw "SE2: SE1 run contract missing: $se1Contract"
}

$expectedEventsSha = "1dca79fdaa452c346d5ff5249d3fb028a8ce33e5788fa6e1e53c89215cf41424"
$actualEventsSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $se1Events).Hash.ToLowerInvariant()
if ($actualEventsSha -ne $expectedEventsSha) {
    throw "SE2: SE1 event SHA256 mismatch. Expected $expectedEventsSha, got $actualEventsSha"
}

$outRoot = Join-Path $root "reports\secondary_entry_se2"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path $outRoot ("ALL9_SE2_DISCOVERY_" + $stamp)
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Host "============================================================="
Write-Host "SE2 SECONDARY ENTRY - CLEAN LAUNCH DISCOVERY V1"
Write-Host "Research only. Downloads: DISABLED / fail-closed."
Write-Host "Input: exact frozen SE1 ALL9 machine truth."
Write-Host "NEW5 access: FORBIDDEN / not used."
Write-Host "Main Entry unchanged. Main structural stop remains -1.00%."
Write-Host "Secondary structural stop remains reversal point - 0.10 percentage points."
Write-Host "Purpose: find causal clean-launch conditions before expensive Scale Entry."
Write-Host "Primary benchmark: Secondary +1.10 before structural stop."
Write-Host "Economics: USD 100 margin x10; cost sensitivity is reported separately."
Write-Host "Candidate families: crossings, timing, rebound speed, and simple 2-filter pairs."
Write-Host "Candidate grid is frozen in code before this run; no NEW5 confirmation here."
Write-Host "============================================================="

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $root "src"
    & $python -m bybit_workbench.research.secondary_entry_se2 `
        --project-root $root `
        --output-dir $outDir `
        --se1-events $se1Events `
        --bootstrap-iterations 2000 `
        --progress-interval-seconds 20
    if ($LASTEXITCODE -ne 0) {
        throw "SE2 discovery failed. Partial output remains isolated in $outDir"
    }
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

$zipPath = Join-Path $outRoot ("ALL9_SE2_DISCOVERY_" + $stamp + ".zip")
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force
Write-Host "SE2 completed."
Write-Host ("Report directory: " + $outDir)
Write-Host ("Result ZIP: " + $zipPath)
Write-Host "Do not use any discovered candidate in production before separate confirmation."
