param(
    [string]$Symbol = "UNIUSDT",
    [string]$P33Dir = "",
    [string]$DatasetDir = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    if (-not $P33Dir) {
        $P33Dir = Get-ChildItem ".\reports\entry_research_v6" -Directory |
            Where-Object {
                (Test-Path (Join-Path $_.FullName "summary.json")) -and
                (Test-Path (Join-Path $_.FullName "signals_adverse_path.csv"))
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $P33Dir) {
            throw "No completed P33 result was found under reports\entry_research_v6."
        }
    }

    Write-Host "P34 P33 source: $P33Dir"
    $args = @(
        "-m", "bybit_workbench.research.entry_open_interest_v4",
        "--symbol", $Symbol,
        "--p33-dir", $P33Dir
    )
    if ($DatasetDir) {
        $args += @("--dataset-dir", $DatasetDir)
    }
    if ($OutputDir) {
        $args += @("--output-dir", $OutputDir)
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "P34 open-interest research failed."
    }
}
finally {
    Pop-Location
}
