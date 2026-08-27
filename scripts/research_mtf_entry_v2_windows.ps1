param(
    [string]$Symbol = "UNIUSDT",
    [int]$Days = 30,
    [string]$Endpoint = "https://api.bybit.kz",
    [int]$FiveLookback = 130,
    [int]$FifteenLookback = 130,
    [int]$HourlyLookback = 130,
    [string]$ConfluenceMaxGapPercent = "0.25",
    [int]$CooldownBars = 12,
    [int]$EmbargoMinutesAfterShock = 60,
    [string]$DatasetDir = ""
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
        "-m", "bybit_workbench.research.mtf_entry_v2",
        "--symbol", $Symbol,
        "--days", "$Days",
        "--endpoint", $Endpoint,
        "--five-lookback", "$FiveLookback",
        "--fifteen-lookback", "$FifteenLookback",
        "--hourly-lookback", "$HourlyLookback",
        "--confluence-max-gap-percent", $ConfluenceMaxGapPercent,
        "--cooldown-bars", "$CooldownBars",
        "--embargo-minutes-after-shock", "$EmbargoMinutesAfterShock"
    )
    if ($DatasetDir) {
        $args += @("--dataset-dir", $DatasetDir)
    }
    & $python @args
    if ($LASTEXITCODE -ne 0) {
        throw "MTF entry research V2 failed."
    }
}
finally {
    Pop-Location
}
