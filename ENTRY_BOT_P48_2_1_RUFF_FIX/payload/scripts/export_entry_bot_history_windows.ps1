param(
    [string]$ProjectRoot = "C:\cripta",
    [string]$OutputPath = "reports\entry_bot_live_audit\entry_bot_history.csv"
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Database = Join-Path $ProjectRoot "var\workbench.db"
$Output = Join-Path $ProjectRoot $OutputPath

if (-not (Test-Path $Python -PathType Leaf)) {
    throw "Python venv not found: $Python"
}
if (-not (Test-Path $Database -PathType Leaf)) {
    throw "Workbench database not found: $Database"
}

Push-Location $ProjectRoot
try {
    & $Python -m bybit_workbench.entry_bot.audit --database $Database --output $Output
    if ($LASTEXITCODE -ne 0) {
        throw "Entry Bot audit export failed."
    }
}
finally {
    Pop-Location
}
