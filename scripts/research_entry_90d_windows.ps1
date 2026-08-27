param(
    [string]$Symbol = "UNIUSDT",
    [int]$Days = 90,
    [string]$Endpoint = "https://api.bybit.kz",
    [int]$FiveLookback = 130,
    [int]$FifteenLookback = 130,
    [int]$HourlyLookback = 130,
    [string]$ConfluenceMaxGapPercent = "0.25",
    [int]$CooldownMinutes = 30,
    [int]$EmbargoMinutesAfterShock = 60,
    [string]$LatestTradeDay = "",
    [string]$DatasetDir = "",
    [string]$OutputDir = "",
    [switch]$SkipOpenInterest
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Push-Location $root
try {
    $args = @(
        "-m", "bybit_workbench.research.mtf_entry_v3",
        "--symbol", $Symbol,
        "--days", "$Days",
        "--endpoint", $Endpoint,
        "--five-lookback", "$FiveLookback",
        "--fifteen-lookback", "$FifteenLookback",
        "--hourly-lookback", "$HourlyLookback",
        "--confluence-max-gap-percent", $ConfluenceMaxGapPercent,
        "--cooldown-minutes", "$CooldownMinutes",
        "--embargo-minutes-after-shock", "$EmbargoMinutesAfterShock"
    )
    if ($LatestTradeDay) {
        $args += @("--latest-trade-day", $LatestTradeDay)
    }
    if ($DatasetDir) {
        $args += @("--dataset-dir", $DatasetDir)
    }
    if ($OutputDir) {
        $args += @("--output-dir", $OutputDir)
    }
    if ($SkipOpenInterest) {
        $args += "--skip-open-interest"
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "P30 90-day entry research failed."
    }
}
finally {
    Pop-Location
}
