param(
    [string]$P39Dir = "",
    [string]$OutputDir = "",
    [string]$ArchiveDir = "",
    [switch]$KeepArchives,
    [int]$MaxDays = 0
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    if (-not $P39Dir) {
        $P39Dir = Get-ChildItem ".\reports\entry_research_v12" -Directory |
            Where-Object {
                (Test-Path (Join-Path $_.FullName "summary.json")) -and
                (Test-Path (Join-Path $_.FullName "orderbook_features.csv"))
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $P39Dir) {
            throw "No completed P39 result was found under reports\entry_research_v12."
        }
    }

    Write-Host "P40 P39 source: $P39Dir"
    $args = @(
        "-m", "bybit_workbench.research.orderbook_absorption_v10",
        "--p39-dir", $P39Dir
    )
    if ($OutputDir) {
        $args += @("--output-dir", $OutputDir)
    }
    if ($ArchiveDir) {
        $args += @("--archive-dir", $ArchiveDir)
    }
    if ($KeepArchives) {
        $args += "--keep-archives"
    }
    if ($MaxDays -gt 0) {
        $args += @("--max-days", "$MaxDays")
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "P40 orderbook absorption research failed. Re-run the same command to resume cached days."
    }
}
finally {
    Pop-Location
}
