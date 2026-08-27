param(
    [string]$Root = "C:\cripta",
    [double]$StartingBankUsd = 100.0,
    [double]$Leverage = 10.0,
    [double]$MakerFeeRate = 0.00020,
    [double]$TakerFeeRate = 0.00055,
    [int]$TimezoneOffsetHours = 5,
    [int]$BurstWindowMinutes = 15,
    [int]$BurstMaxEntries = 2
)

$ErrorActionPreference = "Stop"
$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

Write-Host "====================================================================="
Write-Host "P47L CHRONOLOGICAL PORTFOLIO REPLAY - ALL9"
Write-Host "Frozen Entry V1: unchanged"
Write-Host "P46: unchanged"
Write-Host "Reference bank: $StartingBankUsd USD"
Write-Host "Leverage: ${Leverage}x"
Write-Host "Slot budgets: 50 / 30 / 20 percent; scale down after losses, never above reference bank"
Write-Host "Exit benchmark: +1.10 maker / -1.00 taker / -0.50 taker"
Write-Host "Maker fee rate: $MakerFeeRate"
Write-Host "Taker fee rate: $TakerFeeRate"
Write-Host "Policies: NO CAP and max $BurstMaxEntries entries per $BurstWindowMinutes minutes"
Write-Host "One active position per symbol; max 3 simultaneous positions"
Write-Host "Downloads: DISABLED / compact P47K + P47G reports only"
Write-Host "No upside compounding; finite margin is enforced"
Write-Host "====================================================================="

Push-Location $Root
try {
    & $python -m bybit_workbench.research.portfolio_replay_v25 `
        --root $Root `
        --starting-bank-usd $StartingBankUsd `
        --leverage $Leverage `
        --maker-fee-rate $MakerFeeRate `
        --taker-fee-rate $TakerFeeRate `
        --timezone-offset-hours $TimezoneOffsetHours `
        --burst-window-minutes $BurstWindowMinutes `
        --burst-max-entries $BurstMaxEntries
    if ($LASTEXITCODE -ne 0) {
        throw "P47L portfolio replay failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
