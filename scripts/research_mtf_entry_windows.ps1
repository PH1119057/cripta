param(
    [string]$Symbol = "UNIUSDT",
    [int]$Days = 30,
    [ValidateSet("fixed", "adaptive")]
    [string]$Variant = "fixed",
    [string]$Endpoint = "https://api.bybit.kz",
    [int]$FiveLookback = 130,
    [int]$HourlyLookback = 130,
    [string]$ConfluenceMaxGapPercent = "0.25"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    & $python -m bybit_workbench.research.mtf_entry `
        --symbol $Symbol `
        --days $Days `
        --variant $Variant `
        --endpoint $Endpoint `
        --five-lookback $FiveLookback `
        --hourly-lookback $HourlyLookback `
        --confluence-max-gap-percent $ConfluenceMaxGapPercent
    if ($LASTEXITCODE -ne 0) {
        throw "MTF entry research failed."
    }
}
finally {
    Pop-Location
}
