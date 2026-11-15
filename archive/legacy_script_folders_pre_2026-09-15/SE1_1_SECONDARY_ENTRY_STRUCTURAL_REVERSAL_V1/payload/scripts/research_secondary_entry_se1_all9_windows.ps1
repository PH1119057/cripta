$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$root = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml"))) {
    throw "SE1: run from C:\cripta."
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "SE1: .venv missing; run scripts\setup_windows.ps1 first."
}

$outRoot = Join-Path $root "reports\secondary_entry_se1"
$outDir = Join-Path $outRoot "ALL9_SE1_WORKING"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Host "============================================================="
Write-Host "SE1 SECONDARY ENTRY - STRUCTURAL REVERSAL V1"
Write-Host "Research only. Downloads: DISABLED / fail-closed."
Write-Host "Frozen Main Entry unchanged. Main structural stop remains -1.00%."
Write-Host "Secondary Entry is NEW and uses its own actual fill."
Write-Host "Launch point = causal running adverse extreme before Main -1.00%."
Write-Host "Primary structural stop = launch point minus 0.10 percentage points."
Write-Host "Adverse depth grid: 0.10,0.25,0.50,0.75 percent."
Write-Host "Rebound confirmation grid: 0.10,0.15,0.20,0.25,0.30 percent."
Write-Host "One Secondary attempt per Probe per grid cell. No re-entry loop."
Write-Host "Resume: ENABLED only when run_contract SHA matches exactly."
Write-Host "Illustrative Scale economics: USD 100 margin x10 = USD 1000 notional."
Write-Host "============================================================="

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $root "src"
    & $python -m bybit_workbench.research.secondary_entry_se1 `
        --project-root $root `
        --output-dir $outDir `
        --main-stop-pct 1.00 `
        --structural-buffer-pct 0.10 `
        --horizon-hours 72 `
        --min-adverse-depths-pct "0.10,0.25,0.50,0.75" `
        --rebound-confirmations-pct "0.10,0.15,0.20,0.25,0.30" `
        --scale-margin-usd 100 `
        --scale-leverage 10 `
        --illustrative-round-trip-cost-pct 0.10 `
        --progress-interval-seconds 20
    if ($LASTEXITCODE -ne 0) {
        throw "SE1 research failed. Working output was preserved for exact-contract resume."
    }
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$zipPath = Join-Path $outRoot ("ALL9_SE1_" + $stamp + ".zip")
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force
Write-Host "SE1 completed."
Write-Host ("Working report: " + $outDir)
Write-Host ("Result ZIP: " + $zipPath)
