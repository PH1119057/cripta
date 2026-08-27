param(
    [string]$Symbol = "UNIUSDT",
    [string]$P31Dir = "",
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
    if (-not $P31Dir) {
        $P31Dir = Get-ChildItem ".\reports\entry_research_v4" -Directory |
            Where-Object {
                (Test-Path (Join-Path $_.FullName "summary.json")) -and
                (Test-Path (Join-Path $_.FullName "signals_touch_exact.csv"))
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $P31Dir) {
            throw "No completed P31 result was found under reports\entry_research_v4."
        }
    }

    Write-Host "P32 P31 source: $P31Dir"
    $args = @(
        "-m", "bybit_workbench.research.flow_exhaustion_v2",
        "--symbol", $Symbol,
        "--p31-dir", $P31Dir
    )
    if ($DatasetDir) {
        $args += @("--dataset-dir", $DatasetDir)
    }
    if ($OutputDir) {
        $args += @("--output-dir", $OutputDir)
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "P32 flow-exhaustion research failed."
    }
}
finally {
    Pop-Location
}
