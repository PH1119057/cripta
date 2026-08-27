param(
    [string]$Endpoint = "https://api.bybit.kz",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$unlock = [DateTimeOffset]::Parse("2026-09-18T00:00:00+00:00")
if ([DateTimeOffset]::UtcNow -lt $unlock) {
    throw "P46 data preparation is locked until 2026-09-18 00:00 UTC. Do not build/inspect a partial holdout."
}

$symbols = @(
    "UNIUSDT",
    "LINKUSDT",
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "1000PEPEUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "ADAUSDT"
)

$prefetch = Join-Path $PSScriptRoot "prefetch_multi_asset_90d_windows.ps1"
$assetRunner = Join-Path $PSScriptRoot "research_p46_holdout_asset_windows.ps1"
if (-not (Test-Path $prefetch)) { throw "P43 prefetch script not found: $prefetch" }
if (-not (Test-Path $assetRunner)) { throw "P46 asset runner not found: $assetRunner" }

Write-Host "====================================================================="
Write-Host "P46 HOLDOUT DATA PREPARATION"
Write-Host "Warm-up/data window: 2026-08-12 -> 2026-09-18 UTC (37 days)"
Write-Host "Confirmatory outcomes: 2026-08-19 -> 2026-09-18 UTC only"
Write-Host "Orderbook P39/P40 is intentionally skipped."
Write-Host "====================================================================="

& $prefetch `
    -Symbols $symbols `
    -Days 37 `
    -LatestTradeDay "2026-09-17" `
    -Endpoint $Endpoint `
    -SkipOrderbook
if ($LASTEXITCODE -ne 0) {
    throw "P46 P30 prefetch failed with exit code $LASTEXITCODE"
}

foreach ($symbol in $symbols) {
    $args = @{
        Symbol = $symbol
        Endpoint = $Endpoint
    }
    if ($Force) { $args["Force"] = $true }
    & $assetRunner @args
    if ($LASTEXITCODE -ne 0) {
        throw "P46 asset preparation failed for $symbol with exit code $LASTEXITCODE"
    }
}

Write-Host "====================================================================="
Write-Host "P46 HOLDOUT DATA READY THROUGH P36 FOR ALL 9 ASSETS"
Write-Host "Next: scripts\research_p46_confirmatory_holdout_windows.ps1"
Write-Host "====================================================================="
