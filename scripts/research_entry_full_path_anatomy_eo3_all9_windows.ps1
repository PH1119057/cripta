[CmdletBinding()]
param(
    [string]$EO1Dir = ".\reports\entry_offset_adverse_eo1\ALL9_EO1_20260822_154907",
    [string]$CacheDir = ".\var\eo3_full_path_1m_cache"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$root = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml"))) {
    throw "EO3: run from C:\cripta."
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "EO3: .venv missing; run scripts\setup_windows.ps1 first."
}

function Resolve-UnderRoot([string]$Value) {
    if ([System.IO.Path]::IsPathRooted($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $root $Value))
}

$sourceDir = Resolve-UnderRoot $EO1Dir
$cacheRoot = Resolve-UnderRoot $CacheDir
if (-not (Test-Path -LiteralPath $sourceDir -PathType Container)) {
    throw "EO3: EO1 source report directory does not exist: $sourceDir"
}
New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null

$outRoot = Join-Path $root "reports\entry_full_path_anatomy_eo3"
New-Item -ItemType Directory -Force -Path $outRoot | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outDir = Join-Path $outRoot ("ALL9_EO3_" + $stamp)

Write-Host "============================================================="
Write-Host "EO3 FULL PATH / MFE-GIVEBACK-RETEST ANATOMY V1"
Write-Host "Research only. Downloads: DISABLED / fail-closed."
Write-Host "Cohort: all 846 exact EO1 ADVERSE_0P20 fills. No winner/loser preselection."
Write-Host "Profit target: DISABLED for anatomy. +1.10 and higher levels are milestones only."
Write-Host "Positive floor / BE / trailing: DISABLED."
Write-Host "Trade-life boundary: unchanged hard stop -1.00% or frozen-data end."
Write-Host "Milestones: +0.10/+0.20/+0.30/+0.50/+0.75/+1.00/+1.10/+1.50/+2/+3/+5/+10/+20."
Write-Host "Giveback: causal 1m-close anatomy. Fill and hard-stop boundary minutes use raw ticks."
Write-Host "Post-stop 72h continuation: research-only, never used as live input."
Write-Host "Overlap: later EO1 signals and later -0.20 fills while current trade remains alive."
Write-Host "Cache: resumable per-day 1m cache under var; validated by cache version/symbol/archive stat."
Write-Host "Entry, Exit, Risk, Execution, EO1/EO2, P50/P51/P53, SE1/SE2, MAYAK, live/UI: NOT CHANGED."
Write-Host ("EO1 source: " + $sourceDir)
Write-Host ("EO3 cache: " + $cacheRoot)
Write-Host "============================================================="

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $root "src"
    & $python -m bybit_workbench.research.entry_full_path_anatomy_eo3 `
        --project-root $root `
        --source-report-dir $sourceDir `
        --output-dir $outDir `
        --cache-root $cacheRoot `
        --raw-day-cache-size 3 `
        --heartbeat-seconds 25
    if ($LASTEXITCODE -ne 0) {
        throw "EO3 research failed. Partial output remains isolated in $outDir; validated 1m cache remains reusable in $cacheRoot."
    }
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

$zipPath = $outDir + ".zip"
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force
Write-Host "EO3 completed."
Write-Host ("Report directory: " + $outDir)
Write-Host ("Result ZIP: " + $zipPath)
