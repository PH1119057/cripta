param(
    [string]$P36Dir = "",
    [string]$OutputDir = "",
    [string]$ProbeArchive = "",
    [int]$PreSeconds = 120,
    [int]$PostSeconds = 60
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    if (-not $P36Dir) {
        $P36Dir = Get-ChildItem ".\reports\entry_research_v9" -Directory |
            Where-Object {
                (Test-Path (Join-Path $_.FullName "summary.json")) -and
                (Test-Path (Join-Path $_.FullName "signals_basis.csv"))
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $P36Dir) {
            throw "No completed P36 result was found under reports\entry_research_v9."
        }
    }

    Write-Host "P37 P36 source: $P36Dir"
    $args = @(
        "-m", "bybit_workbench.research.orderbook_plan_v7",
        "--p36-dir", $P36Dir,
        "--pre-seconds", $PreSeconds,
        "--post-seconds", $PostSeconds
    )
    if ($OutputDir) {
        $args += @("--output-dir", $OutputDir)
    }
    if ($ProbeArchive) {
        $args += @("--probe-archive", $ProbeArchive)
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "P37 orderbook planning/probe failed."
    }
}
finally {
    Pop-Location
}
