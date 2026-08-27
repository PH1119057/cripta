param(
    [string]$Symbol = "LINKUSDT",
    [int]$Days = 90,
    [string]$LatestTradeDay = "2026-08-15",
    [string]$Endpoint = "https://api.bybit.kz",
    [string]$ValidationRoot = "",
    [string]$DatasetDir = "",
    [switch]$Force,
    [switch]$AllowOrderbookDownload,
    [int]$OrderbookWorkers = 2
)

$ErrorActionPreference = "Stop"
if ($OrderbookWorkers -lt 1 -or $OrderbookWorkers -gt 4) {
    throw "OrderbookWorkers must be between 1 and 4."
}
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}

$Symbol = $Symbol.Trim().ToUpperInvariant()
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
$canonicalDatasetDir = Join-Path $p30Dir "dataset"
if ($DatasetDir) {
    $datasetDir = [System.IO.Path]::GetFullPath($DatasetDir)
}
else {
    $datasetDir = $canonicalDatasetDir
}
$orderbookCacheDir = Join-Path $datasetDir "orderbook_cache"
$stageStatePath = Join-Path $ValidationRoot "stage_state.json"

New-Item -ItemType Directory -Force -Path $ValidationRoot | Out-Null

function Set-StageState {
    param(
        [string]$Stage,
        [int]$Index,
        [string]$Status = "running",
        [string]$Message = ""
    )
    $payload = [ordered]@{
        symbol = $Symbol
        stage = $Stage
        stage_index = $Index
        stage_total = 10
        status = $Status
        message = $Message
        updated_at = [DateTime]::UtcNow.ToString("o")
        validation_root = $ValidationRoot
        dataset_dir = $datasetDir
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -Path $stageStatePath -Encoding UTF8
}

function Invoke-Checked {
    param(
        [scriptblock]$Action,
        [string]$FailureMessage
    )
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Test-StageFiles {
    param(
        [string]$StageDir,
        [string[]]$RelativeFiles
    )
    foreach ($relative in $RelativeFiles) {
        $path = Join-Path $StageDir $relative
        if (-not (Test-Path $path)) {
            return $false
        }
        if ((Get-Item $path).PSIsContainer) {
            continue
        }
        if ((Get-Item $path).Length -le 0) {
            return $false
        }
    }
    return $true
}

function Write-Resume {
    param(
        [string]$Stage,
        [int]$Index
    )
    Set-StageState -Stage $Stage -Index $Index -Status "reused" -Message "cached outputs reused"
    Write-Host "Resume: $Stage complete; reusing cached outputs."
}

Push-Location $root
try {
    Write-Host "============================================================="
    Write-Host "FROZEN CROSS-ASSET VALIDATION"
    Write-Host "Symbol: $Symbol"
    Write-Host "Period: $expectedStart .. $expectedEnd"
    Write-Host "Dataset: $datasetDir"
    Write-Host "This run must not tune Entry V1 from $Symbol outcomes."
    Write-Host "============================================================="

    $p30Complete = (-not $Force) -and `
        (Test-StageFiles -StageDir $p30Dir -RelativeFiles @(
            "comparison.json",
            "p30_local_soft_hourly\signals.csv"
        )) -and `
        (Test-Path (Join-Path $datasetDir "dataset_manifest.json"))
    if ($p30Complete) {
        Write-Resume -Stage "P30 baseline/frozen dataset" -Index 1
    }
    else {
        Set-StageState -Stage "P30 baseline/frozen dataset" -Index 1
        $p30Args = @{
            Symbol = $Symbol
            Days = $Days
            Endpoint = $Endpoint
            LatestTradeDay = $LatestTradeDay
            OutputDir = $p30Dir
        }
        if ($DatasetDir -or (Test-Path (Join-Path $datasetDir "dataset_manifest.json"))) {
            $p30Args["DatasetDir"] = $datasetDir
        }
        Invoke-Checked -FailureMessage "P30 failed for $Symbol" -Action {
            & (Join-Path $PSScriptRoot "research_entry_90d_windows.ps1") @p30Args
        }
    }

    $p31Complete = (-not $Force) -and (Test-StageFiles -StageDir $p31Dir -RelativeFiles @(
        "summary.json", "signals_touch_exact.csv"
    ))
    if ($p31Complete) {
        Write-Resume -Stage "P31 exact touch / flow reversal" -Index 2
    }
    else {
        Set-StageState -Stage "P31 exact touch / flow reversal" -Index 2
        Invoke-Checked -FailureMessage "P31 failed for $Symbol" -Action {
            & (Join-Path $PSScriptRoot "research_flow_reversal_90d_windows.ps1") `
                -Symbol $Symbol `
                -DatasetDir $datasetDir `
                -OutputDir $p31Dir
        }
    }

    $p33Complete = (-not $Force) -and (Test-StageFiles -StageDir $p33Dir -RelativeFiles @(
        "summary.json", "signals_adverse_path.csv"
    ))
    if ($p33Complete) {
        Write-Resume -Stage "P33 adverse excursion / 60m pause" -Index 3
    }
    else {
        Set-StageState -Stage "P33 adverse excursion / 60m pause" -Index 3
        Invoke-Checked -FailureMessage "P33 failed for $Symbol" -Action {
            & (Join-Path $PSScriptRoot "research_entry_adverse_90d_windows.ps1") `
                -Symbol $Symbol `
                -P31Dir $p31Dir `
                -DatasetDir $datasetDir `
                -OutputDir $p33Dir
        }
    }

    $p34Complete = (-not $Force) -and (Test-StageFiles -StageDir $p34Dir -RelativeFiles @(
        "summary.json", "signals_open_interest.csv"
    ))
    if ($p34Complete) {
        Write-Resume -Stage "P34 open interest" -Index 4
    }
    else {
        Set-StageState -Stage "P34 open interest" -Index 4
        Invoke-Checked -FailureMessage "P34 failed for $Symbol" -Action {
            & (Join-Path $PSScriptRoot "research_open_interest_90d_windows.ps1") `
                -Symbol $Symbol `
                -P33Dir $p33Dir `
                -DatasetDir $datasetDir `
                -OutputDir $p34Dir
        }
    }

    $p35Complete = (-not $Force) -and (Test-StageFiles -StageDir $p35Dir -RelativeFiles @(
        "summary.json", "signals_crowding.csv"
    ))
    if ($p35Complete) {
        Write-Resume -Stage "P35 crowding" -Index 5
    }
    else {
        Set-StageState -Stage "P35 crowding" -Index 5
        Invoke-Checked -FailureMessage "P35 failed for $Symbol" -Action {
            & (Join-Path $PSScriptRoot "research_crowding_90d_windows.ps1") `
                -Symbol $Symbol `
                -Endpoint $Endpoint `
                -P34Dir $p34Dir `
                -DatasetDir $datasetDir `
                -OutputDir $p35Dir
        }
    }

    $p36Complete = (-not $Force) -and (Test-StageFiles -StageDir $p36Dir -RelativeFiles @(
        "summary.json", "signals_basis.csv"
    ))
    if ($p36Complete) {
        Write-Resume -Stage "P36 basis" -Index 6
    }
    else {
        Set-StageState -Stage "P36 basis" -Index 6
        Invoke-Checked -FailureMessage "P36 failed for $Symbol" -Action {
            & (Join-Path $PSScriptRoot "research_basis_90d_windows.ps1") `
                -Symbol $Symbol `
                -Endpoint $Endpoint `
                -P35Dir $p35Dir `
                -DatasetDir $datasetDir `
                -OutputDir $p36Dir
        }
    }

    $p37Complete = (-not $Force) -and (Test-StageFiles -StageDir $p37Dir -RelativeFiles @(
        "summary.json", "orderbook_windows.csv"
    ))
    if ($p37Complete) {
        Write-Resume -Stage "P37 orderbook plan" -Index 7
    }
    else {
        Set-StageState -Stage "P37 orderbook plan" -Index 7
        Invoke-Checked -FailureMessage "P37 failed for $Symbol" -Action {
            & (Join-Path $PSScriptRoot "research_orderbook_plan_windows.ps1") `
                -P36Dir $p36Dir `
                -OutputDir $p37Dir
        }
    }

    $previousLocalOnly = $env:BYBIT_RESEARCH_ORDERBOOK_LOCAL_ONLY
    $previousOrderbookWorkers = $env:BYBIT_RESEARCH_ORDERBOOK_WORKERS
    $env:BYBIT_RESEARCH_ORDERBOOK_WORKERS = "$OrderbookWorkers"
    if ($AllowOrderbookDownload) {
        Remove-Item Env:BYBIT_RESEARCH_ORDERBOOK_LOCAL_ONLY -ErrorAction SilentlyContinue
    }
    else {
        $env:BYBIT_RESEARCH_ORDERBOOK_LOCAL_ONLY = "1"
    }
    try {
        $p39Complete = (-not $Force) -and (Test-StageFiles -StageDir $p39Dir -RelativeFiles @(
            "summary.json", "orderbook_features.csv"
        ))
        if ($p39Complete) {
            Write-Resume -Stage "P39 dynamic orderbook" -Index 8
        }
        else {
            Set-StageState -Stage "P39 dynamic orderbook" -Index 8
            Invoke-Checked -FailureMessage "P39 failed for $Symbol" -Action {
                & (Join-Path $PSScriptRoot "research_orderbook_full_windows.ps1") `
                    -P37Dir $p37Dir `
                    -OutputDir $p39Dir `
                    -ArchiveDir $orderbookCacheDir `
                    -KeepArchives
            }
        }

        $p40Complete = (-not $Force) -and (Test-StageFiles -StageDir $p40Dir -RelativeFiles @(
            "summary.json", "absorption_features.csv"
        ))
        if ($p40Complete) {
            Write-Resume -Stage "P40 absorption" -Index 9
        }
        else {
            Set-StageState -Stage "P40 absorption" -Index 9
            Invoke-Checked -FailureMessage "P40 failed for $Symbol" -Action {
                & (Join-Path $PSScriptRoot "research_orderbook_absorption_windows.ps1") `
                    -P39Dir $p39Dir `
                    -OutputDir $p40Dir `
                    -ArchiveDir $orderbookCacheDir `
                    -KeepArchives
            }
        }
    }
    finally {
        if ($null -eq $previousLocalOnly) {
            Remove-Item Env:BYBIT_RESEARCH_ORDERBOOK_LOCAL_ONLY -ErrorAction SilentlyContinue
        }
        else {
            $env:BYBIT_RESEARCH_ORDERBOOK_LOCAL_ONLY = $previousLocalOnly
        }
        if ($null -eq $previousOrderbookWorkers) {
            Remove-Item Env:BYBIT_RESEARCH_ORDERBOOK_WORKERS -ErrorAction SilentlyContinue
        }
        else {
            $env:BYBIT_RESEARCH_ORDERBOOK_WORKERS = $previousOrderbookWorkers
        }
    }

    $finalComplete = (-not $Force) -and (Test-StageFiles -StageDir $finalDir -RelativeFiles @(
        "validation_summary.json", "validation_summary.md"
    ))
    if ($finalComplete) {
        Write-Resume -Stage "asset summary" -Index 10
    }
    else {
        Set-StageState -Stage "asset summary" -Index 10
        Invoke-Checked -FailureMessage "Cross-asset validation summary failed for $Symbol" -Action {
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
        }
    }

    Set-StageState -Stage "complete" -Index 10 -Status "complete"
    Write-Host "============================================================="
    Write-Host "CROSS-ASSET VALIDATION COMPLETE"
    Write-Host "Result: $(Join-Path $finalDir 'validation_summary.md')"
    Write-Host "Raw public trades stay in: $datasetDir\public_trades"
    Write-Host "Orderbook daily ZIPs stay in: $orderbookCacheDir"
    Write-Host "============================================================="
}
catch {
    Set-StageState -Stage "failed" -Index 0 -Status "failed" -Message $_.Exception.Message
    throw
}
finally {
    Pop-Location
}
