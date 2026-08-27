param(
    [string]$Symbol = "UNIUSDT",
    [string]$DatasetDir = "",
    [string]$OutputDir = "",
    [int]$ExactHorizonMinutes = 360,
    [int]$ImmediateMinutes = 30
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    if (-not $DatasetDir) {
        $DatasetDir = Get-ChildItem ".\reports\entry_research_v3" -Directory -Recurse |
            Where-Object {
                $_.Name -eq "dataset" -and
                (Test-Path (Join-Path $_.FullName "dataset_manifest.json")) -and
                (Test-Path (Join-Path $_.FullName "public_trades"))
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $DatasetDir) {
            throw "No completed P30 dataset with public_trades was found under reports\entry_research_v3."
        }
    }

    Write-Host "P31 frozen dataset: $DatasetDir"
    $args = @(
        "-u",
        "-m", "bybit_workbench.research.flow_reversal_v1",
        "--symbol", $Symbol,
        "--dataset-dir", $DatasetDir,
        "--exact-horizon-minutes", "$ExactHorizonMinutes",
        "--immediate-minutes", "$ImmediateMinutes"
    )
    if ($OutputDir) {
        $args += @("--output-dir", $OutputDir)
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "P31 flow-reversal research failed."
    }
}
finally {
    Pop-Location
}
