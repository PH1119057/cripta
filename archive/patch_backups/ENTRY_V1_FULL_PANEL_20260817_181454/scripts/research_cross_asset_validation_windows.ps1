param(
    [string]$Symbol = "LINKUSDT",
    [int]$Days = 90,
    [string]$LatestTradeDay = "2026-08-15",
    [string]$Endpoint = "https://api.bybit.kz",
    [string]$ValidationRoot = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

$tradeDay = [DateTime]::ParseExact(
    $LatestTradeDay,
    "yyyy-MM-dd",
    [Globalization.CultureInfo]::InvariantCulture
)
$evaluationEndDay = $tradeDay.AddDays(1)
$evaluationStartDay = $evaluationEndDay.AddDays(-$Days)
$expectedStart = $evaluationStartDay.ToString("yyyy-MM-dd") + "T00:00:00+00:00"
$expectedEnd = $evaluationEndDay.ToString("yyyy-MM-dd") + "T00:00:00+00:00"

if (-not $ValidationRoot) {
    $period = $evaluationStartDay.ToString("yyyyMMdd") + "_" + $evaluationEndDay.ToString("yyyyMMdd")
    $ValidationRoot = Join-Path $root "reports\cross_asset_validation\${Symbol}_${period}"
}

$p30Dir = Join-Path $ValidationRoot "p30"
$p31Dir = Join-Path $ValidationRoot "p31"
$p33Dir = Join-Path $ValidationRoot "p33"
$p34Dir = Join-Path $ValidationRoot "p34"
$p35Dir = Join-Path $ValidationRoot "p35"
$p36Dir = Join-Path $ValidationRoot "p36"
$p37Dir = Join-Path $ValidationRoot "p37"
$p39Dir = Join-Path $ValidationRoot "p39"
$p40Dir = Join-Path $ValidationRoot "p40"
$finalDir = Join-Path $ValidationRoot "final"
$datasetDir = Join-Path $p30Dir "dataset"
$orderbookCacheDir = Join-Path $datasetDir "orderbook_cache"

New-Item -ItemType Directory -Force -Path $ValidationRoot | Out-Null

Push-Location $root
try {
    Write-Host "============================================================="
    Write-Host "P41 FROZEN CROSS-ASSET VALIDATION"
    Write-Host "Symbol: $Symbol"
    Write-Host "Period: $expectedStart .. $expectedEnd"
    Write-Host "This run must not tune Entry V1 from $Symbol outcomes."
    Write-Host "============================================================="

    $p30Args = @{
        Symbol = $Symbol
        Days = $Days
        Endpoint = $Endpoint
        LatestTradeDay = $LatestTradeDay
        OutputDir = $p30Dir
    }
    if (Test-Path (Join-Path $datasetDir "dataset_manifest.json")) {
        $p30Args["DatasetDir"] = $datasetDir
    }
    & (Join-Path $PSScriptRoot "research_entry_90d_windows.ps1") @p30Args

    & (Join-Path $PSScriptRoot "research_flow_reversal_90d_windows.ps1") `
        -Symbol $Symbol `
        -DatasetDir $datasetDir `
        -OutputDir $p31Dir

    & (Join-Path $PSScriptRoot "research_entry_adverse_90d_windows.ps1") `
        -Symbol $Symbol `
        -P31Dir $p31Dir `
        -DatasetDir $datasetDir `
        -OutputDir $p33Dir

    & (Join-Path $PSScriptRoot "research_open_interest_90d_windows.ps1") `
        -Symbol $Symbol `
        -P33Dir $p33Dir `
        -DatasetDir $datasetDir `
        -OutputDir $p34Dir

    & (Join-Path $PSScriptRoot "research_crowding_90d_windows.ps1") `
        -Symbol $Symbol `
        -Endpoint $Endpoint `
        -P34Dir $p34Dir `
        -DatasetDir $datasetDir `
        -OutputDir $p35Dir

    & (Join-Path $PSScriptRoot "research_basis_90d_windows.ps1") `
        -Symbol $Symbol `
        -Endpoint $Endpoint `
        -P35Dir $p35Dir `
        -DatasetDir $datasetDir `
        -OutputDir $p36Dir

    & (Join-Path $PSScriptRoot "research_orderbook_plan_windows.ps1") `
        -P36Dir $p36Dir `
        -OutputDir $p37Dir

    & (Join-Path $PSScriptRoot "research_orderbook_full_windows.ps1") `
        -P37Dir $p37Dir `
        -OutputDir $p39Dir `
        -ArchiveDir $orderbookCacheDir `
        -KeepArchives

    & (Join-Path $PSScriptRoot "research_orderbook_absorption_windows.ps1") `
        -P39Dir $p39Dir `
        -OutputDir $p40Dir `
        -ArchiveDir $orderbookCacheDir `
        -KeepArchives

    & $python -m bybit_workbench.research.cross_asset_validation_v11 `
        --symbol $Symbol `
        --p30-dir $p30Dir `
        --p31-dir $p31Dir `
        --p33-dir $p33Dir `
        --p36-dir $p36Dir `
        --p40-dir $p40Dir `
        --expected-start $expectedStart `
        --expected-end $expectedEnd `
        --output-dir $finalDir
    if ($LASTEXITCODE -ne 0) {
        throw "P41 validation summary failed."
    }

    Write-Host "============================================================="
    Write-Host "P41 COMPLETE"
    Write-Host "Result: $(Join-Path $finalDir 'validation_summary.md')"
    Write-Host "Raw $Symbol public trades stay in: $datasetDir\public_trades"
    Write-Host "Orderbook daily ZIPs stay in: $orderbookCacheDir"
    Write-Host "============================================================="
}
finally {
    Pop-Location
}
