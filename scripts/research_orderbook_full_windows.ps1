param(
    [string]$P37Dir = "",
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
    if (-not $P37Dir) {
        $P37Dir = Get-ChildItem ".\reports\entry_research_v10" -Directory |
            Where-Object {
                (Test-Path (Join-Path $_.FullName "summary.json")) -and
                (Test-Path (Join-Path $_.FullName "orderbook_windows.csv"))
            } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
        if (-not $P37Dir) {
            throw "No completed P37 result was found under reports\entry_research_v10."
        }
    }

    Write-Host "P39 P37 source: $P37Dir"
    $args = @(
        "-m", "bybit_workbench.research.orderbook_full_v9",
        "--p37-dir", $P37Dir
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
        throw "P39 full orderbook research failed. Re-run the same command to resume cached days."
    }
}
finally {
    Pop-Location
}
