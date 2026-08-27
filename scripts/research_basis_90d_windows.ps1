param(
    [string]$Symbol = "UNIUSDT",
    [string]$Endpoint = "https://api.bybit.kz",
    [string]$P35Dir = "",
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
    if (-not $P35Dir) {
        $P35Dir = Get-ChildItem ".\reports\entry_research_v8" -Directory |
            Where-Object {
                (Test-Path (Join-Path $_.FullName "summary.json")) -and
                (Test-Path (Join-Path $_.FullName "signals_crowding.csv"))
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $P35Dir) {
            throw "No completed P35 result was found under reports\entry_research_v8."
        }
    }

    Write-Host "P36 P35 source: $P35Dir"
    $args = @(
        "-m", "bybit_workbench.research.entry_basis_v6",
        "--symbol", $Symbol,
        "--endpoint", $Endpoint,
        "--p35-dir", $P35Dir
    )
    if ($DatasetDir) {
        $args += @("--dataset-dir", $DatasetDir)
    }
    if ($OutputDir) {
        $args += @("--output-dir", $OutputDir)
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "P36 basis research failed."
    }
}
finally {
    Pop-Location
}
