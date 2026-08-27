[CmdletBinding()]
param(
    [string]$EO1Dir = ".\reports\entry_offset_adverse_eo1\ALL9_EO1_20260822_154907"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$root = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml"))) {
    throw "EO2: run from C:\cripta."
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "EO2: .venv missing; run scripts\setup_windows.ps1 first."
}

if ([System.IO.Path]::IsPathRooted($EO1Dir)) {
    $sourceDir = [System.IO.Path]::GetFullPath($EO1Dir)
}
else {
    $sourceDir = [System.IO.Path]::GetFullPath((Join-Path $root $EO1Dir))
}
if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) {
    throw "EO2: EO1 source report directory does not exist: $sourceDir"
}

$outRoot = Join-Path $root "reports\entry_offset_no_floor_eo2"
New-Item -ItemType Directory -Force -Path $outRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path $outRoot ("ALL9_EO2_" + $stamp)

Write-Host "============================================================="
Write-Host "EO2 -0.20 ENTRY / NO +0.10 FLOOR / +1.10 VS -1.00 V1"
Write-Host "Research only. Downloads: DISABLED / fail-closed."
Write-Host "Source: exact completed EO1.2 event table, 1063 signals / 846 filled ADVERSE_0P20 trades."
Write-Host "Entry: EO1 actual -0.20 shifted fill; no new Entry search or retune."
Write-Host "After fill: initial stop -1.00% remains; target +1.10%; +0.10 activation/floor DISABLED."
Write-Host "No artificial 72h exit: scan causally until first target/stop tick or frozen-data end."
Write-Host "Frozen end: 2026-08-16T00:00:00Z. Open-at-end trades are censored, not assigned realized PnL."
Write-Host "Economics: same illustrative 0.10% round-trip cost reserve, $100 margin x10."
Write-Host "Signal replay only, NOT portfolio backtest."
Write-Host "Entry, Exit, Risk, Execution, P50/P51/P53, SE1/SE2, MAYAK, live/UI: NOT CHANGED."
Write-Host ("EO1 source: " + $sourceDir)
Write-Host "============================================================="

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $root "src"
    & $python -m bybit_workbench.research.entry_offset_no_floor_eo2 `
        --project-root $root `
        --source-report-dir $sourceDir `
        --output-dir $outDir `
        --progress-interval-seconds 20 `
        --day-cache-size 10
    if ($LASTEXITCODE -ne 0) {
        throw "EO2 research failed. Partial/diagnostic output, if any, remains isolated in $outDir"
    }
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

$zipPath = $outDir + ".zip"
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force
Write-Host "EO2 completed."
Write-Host ("Report directory: " + $outDir)
Write-Host ("Result ZIP: " + $zipPath)
