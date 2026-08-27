param(
    [Parameter(Mandatory = $true)][string]$Symbol,
    [string]$Endpoint = "https://api.bybit.kz",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$Symbol = $Symbol.Trim().ToUpperInvariant()
$period = "20260812_20260918"
$validationRoot = Join-Path $root "reports\cross_asset_validation\${Symbol}_${period}"
$p30Dir = Join-Path $validationRoot "p30"
$p31Dir = Join-Path $validationRoot "p31"
$p33Dir = Join-Path $validationRoot "p33"
$p34Dir = Join-Path $validationRoot "p34"
$p35Dir = Join-Path $validationRoot "p35"
$p36Dir = Join-Path $validationRoot "p36"
$datasetDir = Join-Path $p30Dir "dataset"

if (-not (Test-Path (Join-Path $datasetDir "dataset_manifest.json"))) {
    throw "P46 P30 dataset missing for $Symbol: $datasetDir"
}

function Invoke-Checked {
    param([scriptblock]$Action, [string]$FailureMessage)
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Test-Stage {
    param([string]$Dir, [string[]]$Files)
    if ($Force) { return $false }
    foreach ($file in $Files) {
        $path = Join-Path $Dir $file
        if (-not (Test-Path $path)) { return $false }
        if ((Get-Item $path).Length -le 0) { return $false }
    }
    return $true
}

Write-Host "P46 HOLDOUT ASSET: $Symbol"
Write-Host "P30 dataset reuse: $datasetDir"

if (Test-Stage $p31Dir @("summary.json", "signals_touch_exact.csv")) {
    Write-Host "Resume P31: $Symbol"
}
else {
    Invoke-Checked -FailureMessage "P31 failed for $Symbol" -Action {
        & (Join-Path $PSScriptRoot "research_flow_reversal_90d_windows.ps1") `
            -Symbol $Symbol `
            -DatasetDir $datasetDir `
            -OutputDir $p31Dir
    }
}

if (Test-Stage $p33Dir @("summary.json", "signals_adverse_path.csv")) {
    Write-Host "Resume P33: $Symbol"
}
else {
    Invoke-Checked -FailureMessage "P33 failed for $Symbol" -Action {
        & (Join-Path $PSScriptRoot "research_entry_adverse_90d_windows.ps1") `
            -Symbol $Symbol `
            -P31Dir $p31Dir `
            -DatasetDir $datasetDir `
            -OutputDir $p33Dir
    }
}

if (Test-Stage $p34Dir @("summary.json", "signals_open_interest.csv")) {
    Write-Host "Resume P34: $Symbol"
}
else {
    Invoke-Checked -FailureMessage "P34 failed for $Symbol" -Action {
        & (Join-Path $PSScriptRoot "research_open_interest_90d_windows.ps1") `
            -Symbol $Symbol `
            -P33Dir $p33Dir `
            -DatasetDir $datasetDir `
            -OutputDir $p34Dir
    }
}

if (Test-Stage $p35Dir @("summary.json", "signals_crowding.csv")) {
    Write-Host "Resume P35: $Symbol"
}
else {
    Invoke-Checked -FailureMessage "P35 failed for $Symbol" -Action {
        & (Join-Path $PSScriptRoot "research_crowding_90d_windows.ps1") `
            -Symbol $Symbol `
            -Endpoint $Endpoint `
            -P34Dir $p34Dir `
            -DatasetDir $datasetDir `
            -OutputDir $p35Dir
    }
}

if (Test-Stage $p36Dir @("summary.json", "signals_basis.csv")) {
    Write-Host "Resume P36: $Symbol"
}
else {
    Invoke-Checked -FailureMessage "P36 failed for $Symbol" -Action {
        & (Join-Path $PSScriptRoot "research_basis_90d_windows.ps1") `
            -Symbol $Symbol `
            -Endpoint $Endpoint `
            -P35Dir $p35Dir `
            -DatasetDir $datasetDir `
            -OutputDir $p36Dir
    }
}

Write-Host "P46 HOLDOUT ASSET READY THROUGH P36: $Symbol"
