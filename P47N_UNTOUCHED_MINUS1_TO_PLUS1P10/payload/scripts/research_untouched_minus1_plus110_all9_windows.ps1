$ErrorActionPreference = "Stop"

$root = (Get-Location).Path
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "P47N: .venv missing; run from C:\cripta after setup."
}
if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml"))) {
    throw "P47N: run from C:\cripta."
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outRoot = Join-Path $root "reports\untouched_minus1_plus110_v1"
$outDir = Join-Path $outRoot ("ALL9_" + $stamp)
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

Write-Host "============================================================="
Write-Host "P47N UNTOUCHED -1.00 VS +1.10 EXACT FIRST-TOUCH"
Write-Host "Research only. Downloads: DISABLED."
Write-Host "Frozen Entry V1 unchanged. Exit/Risk/live unchanged."
Write-Host "Rule: from Entry, first +1.10% versus first -1.00%."
Write-Host "No activation, retest, floor, trailing, or runner rule."
Write-Host "Illustrative economics: USD 100 margin, 10x, 0.10% notional cost reserve."
Write-Host "============================================================="

$oldPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = Join-Path $root "src"
    & $python -m bybit_workbench.research.untouched_minus1_plus110_v26 `
        --project-root $root `
        --output-dir $outDir `
        --margin-usd 100 `
        --leverage 10 `
        --illustrative-round-trip-cost-pct 0.10
    if ($LASTEXITCODE -ne 0) {
        throw "P47N research failed."
    }
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}

$zipPath = $outDir + ".zip"
Compress-Archive -Path (Join-Path $outDir "*") -DestinationPath $zipPath -Force
Write-Host "P47N completed."
Write-Host ("Report: " + $outDir)
Write-Host ("ZIP: " + $zipPath)
