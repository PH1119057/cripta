param(
    [string]$Symbol = "UNIUSDT",
    [string]$Endpoint = "https://api.bybit.kz",
    [string]$P34Dir = "",
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
    if (-not $P34Dir) {
        $P34Dir = Get-ChildItem ".\reports\entry_research_v7" -Directory |
            Where-Object {
                (Test-Path (Join-Path $_.FullName "summary.json")) -and
                (Test-Path (Join-Path $_.FullName "signals_open_interest.csv"))
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $P34Dir) {
            throw "No completed P34 result was found under reports\entry_research_v7."
        }
    }

    Write-Host "P35 P34 source: $P34Dir"
    $args = @(
        "-m", "bybit_workbench.research.entry_crowding_v5",
        "--symbol", $Symbol,
        "--endpoint", $Endpoint,
        "--p34-dir", $P34Dir
    )
    if ($DatasetDir) {
        $args += @("--dataset-dir", $DatasetDir)
    }
    if ($OutputDir) {
        $args += @("--output-dir", $OutputDir)
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "P35 crowding research failed."
    }
}
finally {
    Pop-Location
}
