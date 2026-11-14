$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python venv not found: $Python"
}

Write-Host "============================================================="
Write-Host "ZS1 ZONE-ASSISTED SECONDARY ENTRY V1"
Write-Host "Research only. Downloads: DISABLED. NEW5: NOT ACCESSED."
Write-Host "Frozen Entry/Exit/Risk/Execution/live: NOT CHANGED."
Write-Host "Parent Secondary: SE1 A0.50 / R0.30. GOLD: SE2 ZLE3."
Write-Host "============================================================="

Push-Location $Root
$env:PYTHONPATH = (Join-Path $Root "src")
try {
    & $Python -m bybit_workbench.research.secondary_entry_zone_scale_zs1 --root $Root
    if ($LASTEXITCODE -ne 0) {
        throw "ZS1 research failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
