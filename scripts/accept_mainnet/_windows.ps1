param(
    [string]$Symbol = "UNIUSDT",
    [string]$Endpoint = "https://api.bybit.kz",
    [string]$ReportPath = ".\var\mainnet_acceptance.json"
)
$ErrorActionPreference = "Stop"
$target = Join-Path (Split-Path -Parent $PSScriptRoot) "accept_mainnet_windows.ps1"
& $target -Symbol $Symbol -Endpoint $Endpoint -ReportPath $ReportPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
