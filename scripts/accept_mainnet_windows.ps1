param(
    [string]$Symbol = "UNIUSDT",
    [string]$Endpoint = "https://api.bybit.kz",
    [string]$ReportPath = ".\var\mainnet_acceptance.json"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
Set-Location (Split-Path -Parent $PSScriptRoot)

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python -PathType Leaf)) {
    throw ".venv is missing. Run scripts\setup\_windows.ps1 first."
}
if ($Endpoint -notin @("https://api.bybit.kz", "https://api.bybit.com")) {
    throw "Pass 7 accepts only the approved Bybit Mainnet endpoints."
}
if ([string]::IsNullOrWhiteSpace($Symbol)) {
    throw "Symbol is required."
}

$oldProfile = $env:BYBIT_WORKBENCH_PROFILE
$oldRest = $env:BYBIT_WORKBENCH_REST_URL
$oldAllowLive = $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING
$oldTestnet = $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION
$oldPublicWs = $env:BYBIT_WORKBENCH_PUBLIC_WS_URL
$oldPrivateWs = $env:BYBIT_WORKBENCH_PRIVATE_WS_URL
try {
    # Pass 7 is deliberately fail-closed. The CLI path owns no write transport.
    $env:BYBIT_WORKBENCH_PROFILE = "live"
    $env:BYBIT_WORKBENCH_REST_URL = $Endpoint
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = "0"
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = "0"
    Remove-Item Env:BYBIT_WORKBENCH_PUBLIC_WS_URL -ErrorAction SilentlyContinue
    Remove-Item Env:BYBIT_WORKBENCH_PRIVATE_WS_URL -ErrorAction SilentlyContinue

    Write-Host "=== PASS 7 / MAINNET GET-ONLY ACCEPTANCE ==="
    Write-Host "endpoint=$Endpoint symbol=$($Symbol.ToUpperInvariant()) live_switch=OFF"
    & $python -m bybit_workbench --mainnet-acceptance `
        --symbol $Symbol.ToUpperInvariant() `
        --acceptance-report $ReportPath
    if ($LASTEXITCODE -ne 0) {
        throw "Mainnet GET-only acceptance failed. No trading mutation was attempted."
    }

    if (-not (Test-Path $ReportPath -PathType Leaf)) {
        throw "Acceptance report was not created."
    }
    $report = Get-Content $ReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.secret_material_included -ne $false) {
        throw "Acceptance report hygiene check failed: secret material flag is not false."
    }
    if ($report.api_key_value_included -ne $false) {
        throw "Acceptance report hygiene check failed: API key flag is not false."
    }
    if ($report.ip_addresses_included -ne $false) {
        throw "Acceptance report hygiene check failed: IP addresses flag is not false."
    }

    Write-Host "=== PASS 7 RESULT ==="
    Write-Host "micro_live_ready=$($report.micro_live_ready)"
    foreach ($check in $report.checks) {
        $state = if ($check.passed) { "PASS" } else { "BLOCK" }
        Write-Host ("{0,-5} {1,-32} {2}" -f $state, $check.code, $check.detail)
    }
    Write-Host "report=$((Resolve-Path $ReportPath).Path)"
    Write-Host "sha256_file=$((Resolve-Path ($ReportPath + '.sha256')).Path)"
    Write-Host "PASS 7 GET-only acceptance report generated successfully."
}
finally {
    $env:BYBIT_WORKBENCH_PROFILE = $oldProfile
    $env:BYBIT_WORKBENCH_REST_URL = $oldRest
    $env:BYBIT_WORKBENCH_ALLOW_LIVE_TRADING = $oldAllowLive
    $env:BYBIT_WORKBENCH_ENABLE_TESTNET_EXECUTION = $oldTestnet
    $env:BYBIT_WORKBENCH_PUBLIC_WS_URL = $oldPublicWs
    $env:BYBIT_WORKBENCH_PRIVATE_WS_URL = $oldPrivateWs
}
