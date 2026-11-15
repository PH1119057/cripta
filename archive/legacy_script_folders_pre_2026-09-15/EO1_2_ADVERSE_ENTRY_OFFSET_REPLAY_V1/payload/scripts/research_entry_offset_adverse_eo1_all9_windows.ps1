[CmdletBinding()]
param(
    [string]$ResumeDir = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

$root = (Get-Location).Path
if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml"))) {
    throw "EO1: run from C:\cripta."
}
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "EO1: .venv missing; run scripts\setup_windows.ps1 first."
}

$outRoot = Join-Path $root "reports\entry_offset_adverse_eo1"
New-Item -ItemType Directory -Force -Path $outRoot | Out-Null
if ([string]::IsNullOrWhiteSpace($ResumeDir)) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outDir = Join-Path $outRoot ("ALL9_EO1_" + $stamp)
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
}
else {
    if ([System.IO.Path]::IsPathRooted($ResumeDir)) {
        $candidate = [System.IO.Path]::GetFullPath($ResumeDir)
    }
    else {
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $root $ResumeDir))
    }
    $outRootFull = [System.IO.Path]::GetFullPath($outRoot).TrimEnd("\") + "\"
    $candidateFull = $candidate.TrimEnd("\")
    if (-not $candidateFull.StartsWith($outRootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "EO1: ResumeDir must be inside reports\entry_offset_adverse_eo1."
    }
    if (-not (Test-Path -LiteralPath $candidateFull -PathType Container)) {
        throw "EO1: ResumeDir does not exist: $candidateFull"
    }
    foreach ($required in @(
        "run_contract.json",
        "entry_offset_adverse_events.partial.jsonl"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $candidateFull $required))) {
            throw "EO1: ResumeDir is missing $required"
        }
    }
    $outDir = $candidateFull
}

Write-Host "============================================================="
Write-Host "EO1 ADVERSE ENTRY OFFSET REPLAY V1 / ENGINE EO1.2"
Write-Host "Research only. Downloads: DISABLED / fail-closed."
Write-Host "Frozen ALL9 exact-touch signals: expected 1063."
Write-Host "Long pending entries: original Entry -0.10% and -0.20%."
Write-Host "Short pending entries: original Entry +0.10% and +0.20%."
Write-Host "Control: original 0.00 Entry."
Write-Host "Pending window: 72h; cancel if original +1.10 target happens first."
Write-Host "After shifted fill: stop -1.00%; activate at +0.10%; floor +0.10%; target +1.10%."
Write-Host "Trade observation horizon: 72h from actual shifted fill."
Write-Host "Engine EO1.2 scans packed daily tape without materializing a 144h Python path."
Write-Host "+0.10 floor is theoretical price protection, not guaranteed net economic BE."
Write-Host "Entry, Exit, Risk, Execution, live/UI: NOT CHANGED."
if (-not [string]::IsNullOrWhiteSpace($ResumeDir)) {
    Write-Host ("Resume directory: " + $outDir)
}
Write-Host "============================================================="

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $root "src"
    & $python -m bybit_workbench.research.entry_offset_adverse_eo1 `
        --project-root $root `
        --output-dir $outDir `
        --progress-interval-seconds 20 `
        --day-cache-size 10
    if ($LASTEXITCODE -ne 0) {
        throw "EO1 research failed. Partial output remains isolated in $outDir"
    }
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

$leaf = Split-Path -Leaf $outDir
$zipPath = Join-Path $outRoot ($leaf + ".zip")
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force
Write-Host "EO1 completed."
Write-Host ("Report directory: " + $outDir)
Write-Host ("Result ZIP: " + $zipPath)
