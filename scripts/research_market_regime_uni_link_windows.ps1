param(
    [string]$Endpoint = "https://api.bybit.kz",
    [string]$Start = "2026-05-18T00:00:00+00:00",
    [string]$End = "2026-08-16T00:00:00+00:00"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    & $python (Join-Path $PSScriptRoot "research_market_regime_uni_link.py") `
        --root $root `
        --endpoint $Endpoint `
        --start $Start `
        --end $End
    if ($LASTEXITCODE -ne 0) {
        throw "P44 market-regime proxy probe failed."
    }
}
finally {
    Pop-Location
}
