param(
    [string[]]$Symbols = @(
        "UNIUSDT",
        "LINKUSDT",
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "1000PEPEUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "ADAUSDT"
    ),
    [int]$Days = 90,
    [string]$LatestTradeDay = "2026-08-15",
    [string]$Endpoint = "https://api.bybit.kz",
    [int]$HeartbeatSeconds = 20,
    [int]$OrderbookWorkers = 2,
    [switch]$Force,
    [switch]$AllowDownload
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$assetRunner = Join-Path $PSScriptRoot "research_cross_asset_validation_windows.ps1"
if (-not (Test-Path $python)) {
    throw "Python environment not found: $python"
}
if (-not (Test-Path $assetRunner)) {
    throw "Cross-asset runner not found: $assetRunner"
}
if ($HeartbeatSeconds -lt 5) {
    throw "HeartbeatSeconds must be at least 5."
}
if ($OrderbookWorkers -lt 1 -or $OrderbookWorkers -gt 4) {
    throw "OrderbookWorkers must be between 1 and 4."
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
$period = $evaluationStartDay.ToString("yyyyMMdd") + "_" + $evaluationEndDay.ToString("yyyyMMdd")
$validationBase = Join-Path $root "reports\cross_asset_validation"
$panelOutput = Join-Path $validationBase "ENTRY_V1_FULL_PANEL_${period}"
New-Item -ItemType Directory -Force -Path $validationBase | Out-Null

$normalizedSymbols = @()
foreach ($rawSymbol in $Symbols) {
    $symbol = $rawSymbol.Trim().ToUpperInvariant()
    if ($symbol -and $normalizedSymbols -notcontains $symbol) {
        $normalizedSymbols += $symbol
    }
}
if ($normalizedSymbols.Count -eq 0) {
    throw "At least one symbol is required."
}

function Test-FrozenManifest {
    param(
        [string]$DatasetDir,
        [string]$Symbol
    )
    $manifestPath = Join-Path $DatasetDir "dataset_manifest.json"
    if (-not (Test-Path $manifestPath)) {
        return $false
    }
    try {
        $manifest = Get-Content -Path $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([string]$manifest.symbol -ne $Symbol) { return $false }
        if ([int]$manifest.days -ne $Days) { return $false }
        if ([string]$manifest.latest_complete_trade_day -ne $LatestTradeDay) { return $false }
        if ([string]$manifest.evaluation_start -ne $expectedStart) { return $false }
        if ([string]$manifest.evaluation_end -ne $expectedEnd) { return $false }
        return $true
    }
    catch {
        return $false
    }
}

function Find-FrozenDataset {
    param([string]$Symbol)

    $canonical = Join-Path $validationBase "${Symbol}_${period}\p30\dataset"
    if (Test-FrozenManifest -DatasetDir $canonical -Symbol $Symbol) {
        return $canonical
    }

    $legacyRoot = Join-Path $root "reports\entry_research_v3"
    if (Test-Path $legacyRoot) {
        $candidates = @(
            Get-ChildItem -Path $legacyRoot -Directory -Filter "${Symbol}_*" -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending
        )
        foreach ($candidate in $candidates) {
            $dataset = Join-Path $candidate.FullName "dataset"
            if (Test-FrozenManifest -DatasetDir $dataset -Symbol $Symbol) {
                return $dataset
            }
        }
    }
    return ""
}

function Test-PipelineComplete {
    param([string]$ValidationRoot)
    $required = @(
        "p30\comparison.json",
        "p30\p30_local_soft_hourly\signals.csv",
        "p31\summary.json",
        "p33\summary.json",
        "p33\signals_adverse_path.csv",
        "p34\summary.json",
        "p34\signals_open_interest.csv",
        "p35\summary.json",
        "p35\signals_crowding.csv",
        "p36\summary.json",
        "p36\signals_basis.csv",
        "p39\summary.json",
        "p39\orderbook_features.csv",
        "p40\summary.json",
        "p40\absorption_features.csv"
    )
    foreach ($relative in $required) {
        if (-not (Test-Path (Join-Path $ValidationRoot $relative))) {
            return $false
        }
    }
    return $true
}

function Find-LegacyStageDir {
    param(
        [string]$Version,
        [string]$Symbol,
        [string]$MetadataFile
    )
    $base = Join-Path $root "reports\entry_research_${Version}"
    if (-not (Test-Path $base)) {
        return ""
    }
    $candidates = @(
        Get-ChildItem -Path $base -Directory -Filter "${Symbol}_*" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending
    )
    foreach ($candidate in $candidates) {
        $metadataPath = Join-Path $candidate.FullName $MetadataFile
        if (-not (Test-Path $metadataPath)) {
            continue
        }
        try {
            $metadata = Get-Content -Path $metadataPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $start = [string]$metadata.evaluation_start
            $end = [string]$metadata.evaluation_end
            if ((-not $start -or -not $end) -and $metadata.p36_dir) {
                $p36SummaryPath = Join-Path ([string]$metadata.p36_dir) "summary.json"
                if (Test-Path $p36SummaryPath) {
                    $p36Metadata = Get-Content -Path $p36SummaryPath -Raw -Encoding UTF8 |
                        ConvertFrom-Json
                    $start = [string]$p36Metadata.evaluation_start
                    $end = [string]$p36Metadata.evaluation_end
                }
            }
            if ($start -ne $expectedStart) { continue }
            if ($end -ne $expectedEnd) { continue }
            return $candidate.FullName
        }
        catch {
            continue
        }
    }
    return ""
}

function Copy-StageFiles {
    param(
        [string]$SourceDir,
        [string]$DestinationDir,
        [string[]]$RelativeFiles
    )
    foreach ($relative in $RelativeFiles) {
        $source = Join-Path $SourceDir $relative
        if (-not (Test-Path $source)) {
            throw "Legacy stage file missing: $source"
        }
        $destination = Join-Path $DestinationDir $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destination) | Out-Null
        Copy-Item -Path $source -Destination $destination -Force
    }
}

function Materialize-LegacyFrozenOutputs {
    param(
        [string]$Symbol,
        [string]$ValidationRoot
    )
    if (Test-PipelineComplete -ValidationRoot $ValidationRoot) {
        return $true
    }

    $stageSpecs = @(
        [pscustomobject]@{ key = "p30"; version = "v3"; metadata = "comparison.json"; files = @("comparison.json", "p30_local_soft_hourly\signals.csv") },
        [pscustomobject]@{ key = "p31"; version = "v4"; metadata = "summary.json"; files = @("summary.json") },
        [pscustomobject]@{ key = "p33"; version = "v6"; metadata = "summary.json"; files = @("summary.json", "signals_adverse_path.csv") },
        [pscustomobject]@{ key = "p34"; version = "v7"; metadata = "summary.json"; files = @("summary.json", "signals_open_interest.csv") },
        [pscustomobject]@{ key = "p35"; version = "v8"; metadata = "summary.json"; files = @("summary.json", "signals_crowding.csv") },
        [pscustomobject]@{ key = "p36"; version = "v9"; metadata = "summary.json"; files = @("summary.json", "signals_basis.csv") },
        [pscustomobject]@{ key = "p39"; version = "v12"; metadata = "summary.json"; files = @("summary.json", "orderbook_features.csv") },
        [pscustomobject]@{ key = "p40"; version = "v13"; metadata = "summary.json"; files = @("summary.json", "absorption_features.csv") }
    )

    $sources = [ordered]@{}
    foreach ($spec in $stageSpecs) {
        $sourceDir = Find-LegacyStageDir `
            -Version ([string]$spec.version) `
            -Symbol $Symbol `
            -MetadataFile ([string]$spec.metadata)
        if (-not $sourceDir) {
            return $false
        }
        foreach ($relative in @($spec.files)) {
            if (-not (Test-Path (Join-Path $sourceDir $relative))) {
                return $false
            }
        }
        $sources[[string]$spec.key] = $sourceDir
    }

    Write-Host "Legacy frozen outputs found for $Symbol; materializing small report files only."
    foreach ($spec in $stageSpecs) {
        $destinationDir = Join-Path $ValidationRoot ([string]$spec.key)
        Copy-StageFiles `
            -SourceDir ([string]$sources[[string]$spec.key]) `
            -DestinationDir $destinationDir `
            -RelativeFiles @($spec.files)
    }
    New-Item -ItemType Directory -Force -Path $ValidationRoot | Out-Null
    $provenance = [ordered]@{
        symbol = $Symbol
        evaluation_start = $expectedStart
        evaluation_end = $expectedEnd
        mode = "legacy_report_materialization"
        heavy_datasets_copied = $false
        sources = $sources
        created_at = [DateTime]::UtcNow.ToString("o")
    }
    $provenance | ConvertTo-Json -Depth 8 |
        Set-Content -Path (Join-Path $ValidationRoot "legacy_materialization.json") -Encoding UTF8
    return (Test-PipelineComplete -ValidationRoot $ValidationRoot)
}

function Test-OrderbookPrefetchReady {
    param(
        [string]$ValidationRoot,
        [string]$DatasetDir
    )
    $cacheDir = Join-Path $DatasetDir "orderbook_cache"
    if (-not (Test-Path $cacheDir)) {
        return $false
    }
    $planPath = Join-Path $ValidationRoot "orderbook_prefetch_plan.csv"
    if (Test-Path $planPath) {
        try {
            $rows = @(Import-Csv -Path $planPath)
            $required = @($rows | Where-Object { $_.status -ne "missing" -and $_.filename })
            if ($required.Count -eq 0) {
                return $false
            }
            foreach ($row in $required) {
                $archive = Join-Path $cacheDir ([string]$row.filename)
                if (-not (Test-Path $archive)) {
                    return $false
                }
            }
            return $true
        }
        catch {
            return $false
        }
    }

    $archives = @(
        Get-ChildItem -Path $cacheDir -File -Filter "*_ob*.data.zip" -ErrorAction SilentlyContinue
    )
    return $archives.Count -ge 50
}

function Format-Duration {
    param([TimeSpan]$Value)
    if ($Value.TotalHours -ge 1) {
        return ("{0:00}:{1:00}:{2:00}" -f [int]$Value.TotalHours, $Value.Minutes, $Value.Seconds)
    }
    return ("{0:00}:{1:00}" -f $Value.Minutes, $Value.Seconds)
}

function Read-StageState {
    param([string]$ValidationRoot)
    $path = Join-Path $ValidationRoot "stage_state.json"
    if (-not (Test-Path $path)) {
        return $null
    }
    try {
        return Get-Content -Path $path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

$datasetBySymbol = @{}
$pipelineCompleteBySymbol = @{}
$missingDatasets = @()
$missingOrderbookCaches = @()
foreach ($symbol in $normalizedSymbols) {
    $validationRoot = Join-Path $validationBase "${symbol}_${period}"
    $isComplete = (-not $Force) -and (Test-PipelineComplete -ValidationRoot $validationRoot)
    if ((-not $Force) -and (-not $isComplete)) {
        $isComplete = Materialize-LegacyFrozenOutputs `
            -Symbol $symbol `
            -ValidationRoot $validationRoot
    }
    $pipelineCompleteBySymbol[$symbol] = $isComplete
    if ($isComplete) {
        $datasetBySymbol[$symbol] = ""
        continue
    }
    $dataset = Find-FrozenDataset -Symbol $symbol
    $datasetBySymbol[$symbol] = $dataset
    if (-not $dataset -and -not $AllowDownload) {
        $missingDatasets += $symbol
        continue
    }
    if ($dataset -and -not $AllowDownload) {
        $orderbookReady = Test-OrderbookPrefetchReady `
            -ValidationRoot $validationRoot `
            -DatasetDir $dataset
        if (-not $orderbookReady) {
            $missingOrderbookCaches += $symbol
        }
    }
}
if ($missingDatasets.Count -gt 0) {
    throw (
        "Frozen datasets are missing for: $($missingDatasets -join ', '). " +
        "No research stage was started. Re-run P43/copy the missing cache, or use " +
        "-AllowDownload explicitly."
    )
}
if ($missingOrderbookCaches.Count -gt 0) {
    throw (
        "Heavy orderbook cache is incomplete for: $($missingOrderbookCaches -join ', '). " +
        "No research stage was started, so P39/P40 cannot trigger surprise downloads. " +
        "Restore the P43 cache or use -AllowDownload explicitly."
    )
}

Write-Host "====================================================================="
Write-Host "AUX CONTEXT PREFLIGHT"
Write-Host "Small Bybit REST context is prefetched/reused BEFORE heavy research."
Write-Host "A transient network failure retries here and cannot waste hours of compute."
Write-Host "====================================================================="
$previousPythonUnbuffered = $env:PYTHONUNBUFFERED
$env:PYTHONUNBUFFERED = "1"
try {
    foreach ($symbol in $normalizedSymbols) {
        if ([bool]$pipelineCompleteBySymbol[$symbol]) {
            Write-Host "AUX context: $symbol pipeline complete; skip."
            continue
        }
        $datasetDir = [string]$datasetBySymbol[$symbol]
        if (-not $datasetDir) {
            if ($AllowDownload) {
                Write-Host "AUX context: $symbol deferred until its dataset exists."
                continue
            }
            throw "AUX context preflight has no frozen dataset for $symbol."
        }
        Write-Host "AUX context: $symbol"
        & $python -m bybit_workbench.research.entry_aux_prefetch `
            --symbol $symbol `
            --endpoint $Endpoint `
            --dataset-dir $datasetDir `
            --evaluation-start $expectedStart `
            --evaluation-end $expectedEnd
        if ($LASTEXITCODE -ne 0) {
            throw (
                "AUX context preflight failed for $symbol. Heavy research was NOT started. " +
                "Existing stage outputs remain reusable; restore the network and rerun."
            )
        }
    }
}
finally {
    if ($null -eq $previousPythonUnbuffered) {
        Remove-Item Env:PYTHONUNBUFFERED -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONUNBUFFERED = $previousPythonUnbuffered
    }
}

$panelStarted = Get-Date
$totalStages = $normalizedSymbols.Count * 10
$completedStages = 0

Write-Host "====================================================================="
Write-Host "ENTRY V1 FULL CROSS-ASSET VALIDATION"
Write-Host "Frozen period: $expectedStart .. $expectedEnd"
Write-Host "Symbols: $($normalizedSymbols -join ', ')"
Write-Host "Heavy frozen downloads: $(if ($AllowDownload) { 'allowed explicitly' } else { 'DISABLED / fail-closed' })"
Write-Host "Aux REST context: prefetched/reused before heavy compute"
Write-Host "Orderbook analysis workers: $OrderbookWorkers"
Write-Host "Heartbeat: every $HeartbeatSeconds seconds"
Write-Host "Live trading logic is NOT changed by this script."
Write-Host "====================================================================="

for ($symbolIndex = 0; $symbolIndex -lt $normalizedSymbols.Count; $symbolIndex++) {
    $symbol = $normalizedSymbols[$symbolIndex]
    $validationRoot = Join-Path $validationBase "${symbol}_${period}"
    $datasetDir = [string]$datasetBySymbol[$symbol]

    Write-Host ""
    Write-Host "#####################################################################"
    Write-Host "ASSET $($symbolIndex + 1)/$($normalizedSymbols.Count): $symbol"
    Write-Host "Validation root: $validationRoot"
    if ([bool]$pipelineCompleteBySymbol[$symbol]) {
        Write-Host "Pipeline already complete; reusing cached outputs for $symbol."
        Write-Host "#####################################################################"
        $completedStages += 10
        continue
    }
    if ($datasetDir) {
        Write-Host "Frozen dataset: $datasetDir"
    }
    elseif (-not $AllowDownload) {
        throw (
            "Frozen dataset not found for $symbol. Expected the P43/cached manifest for " +
            "$expectedStart .. $expectedEnd. No download was started."
        )
    }
    else {
        Write-Warning "Frozen dataset not found for $symbol; runner may download it because -AllowDownload was set."
    }
    Write-Host "#####################################################################"

    New-Item -ItemType Directory -Force -Path $validationRoot | Out-Null
    $job = Start-Job -ScriptBlock {
        param(
            [string]$ScriptPath,
            [string]$JobSymbol,
            [int]$JobDays,
            [string]$JobLatestTradeDay,
            [string]$JobEndpoint,
            [string]$JobValidationRoot,
            [string]$JobDatasetDir,
            [bool]$JobForce,
            [bool]$JobAllowDownload,
            [int]$JobOrderbookWorkers
        )
        $ErrorActionPreference = "Stop"
        $env:PYTHONUNBUFFERED = "1"
        $params = @{
            Symbol = $JobSymbol
            Days = $JobDays
            LatestTradeDay = $JobLatestTradeDay
            Endpoint = $JobEndpoint
            ValidationRoot = $JobValidationRoot
        }
        if ($JobDatasetDir) {
            $params["DatasetDir"] = $JobDatasetDir
        }
        if ($JobForce) {
            $params["Force"] = $true
        }
        if ($JobAllowDownload) {
            $params["AllowOrderbookDownload"] = $true
        }
        $params["OrderbookWorkers"] = $JobOrderbookWorkers
        & $ScriptPath @params *>&1
    } -ArgumentList @(
        $assetRunner,
        $symbol,
        $Days,
        $LatestTradeDay,
        $Endpoint,
        $validationRoot,
        $datasetDir,
        [bool]$Force,
        [bool]$AllowDownload,
        $OrderbookWorkers
    )

    $lastHeartbeat = Get-Date
    $lastStageIndex = 0
    try {
        while ($job.State -in @("NotStarted", "Running")) {
            $receiveErrors = @()
            $output = @(Receive-Job -Job $job -ErrorAction SilentlyContinue -ErrorVariable +receiveErrors)
            foreach ($line in $output) {
                Write-Host $line
            }
            foreach ($record in $receiveErrors) {
                Write-Host ("[job stderr] " + $record.ToString()) -ForegroundColor Red
            }

            $state = Read-StageState -ValidationRoot $validationRoot
            if ($null -ne $state) {
                $lastStageIndex = [Math]::Max(0, [int]$state.stage_index)
            }

            $now = Get-Date
            if (($now - $lastHeartbeat).TotalSeconds -ge $HeartbeatSeconds) {
                $elapsed = $now - $panelStarted
                $inAssetDone = [Math]::Max(0, [Math]::Min(9, $lastStageIndex - 1))
                $processed = [Math]::Min($totalStages, $completedStages + $inAssetDone)
                $percent = if ($totalStages -gt 0) { $processed * 100.0 / $totalStages } else { 0.0 }
                $etaText = "unknown"
                if ($processed -gt 0) {
                    $secondsPerStage = $elapsed.TotalSeconds / $processed
                    $remainingSeconds = [Math]::Max(0.0, ($totalStages - $processed) * $secondsPerStage)
                    $etaText = Format-Duration -Value ([TimeSpan]::FromSeconds($remainingSeconds))
                }
                $stageName = if ($null -ne $state) { [string]$state.stage } else { "starting" }
                $heartbeatLine = (
                    "[heartbeat] asset={0}/{1} symbol={2} stage={3}/10 '{4}' " +
                    "panel_stages={5}/{6} ({7:N1}%) elapsed={8} panel_ETA_rough~{9}"
                ) -f @(
                    ($symbolIndex + 1),
                    $normalizedSymbols.Count,
                    $symbol,
                    $lastStageIndex,
                    $stageName,
                    $processed,
                    $totalStages,
                    $percent,
                    (Format-Duration -Value $elapsed),
                    $etaText
                )
                Write-Host $heartbeatLine
                $lastHeartbeat = $now
            }
            Start-Sleep -Seconds 2
            $job = Get-Job -Id $job.Id
        }

        $receiveErrors = @()
        $output = @(Receive-Job -Job $job -ErrorAction SilentlyContinue -ErrorVariable +receiveErrors)
        foreach ($line in $output) {
            Write-Host $line
        }
        foreach ($record in $receiveErrors) {
            Write-Host ("[job stderr] " + $record.ToString()) -ForegroundColor Red
        }
        if ($job.State -ne "Completed") {
            $reason = if ($job.ChildJobs.Count -gt 0) { $job.ChildJobs[0].JobStateInfo.Reason } else { $null }
            throw "Validation job failed for $symbol. $reason"
        }
        if (-not (Test-PipelineComplete -ValidationRoot $validationRoot)) {
            throw "Validation job completed but required outputs are missing for $symbol."
        }
        $completedStages += 10
    }
    finally {
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "====================================================================="
Write-Host "ALL ASSET PIPELINES COMPLETE. Building asset-balanced panel..."
Write-Host "====================================================================="

$aggregateArgs = @(
    "-m", "bybit_workbench.research.full_panel_entry_validation",
    "--validation-base", $validationBase,
    "--output-dir", $panelOutput,
    "--expected-start", $expectedStart,
    "--expected-end", $expectedEnd,
    "--symbols"
)
$aggregateArgs += $normalizedSymbols
& $python @aggregateArgs
if ($LASTEXITCODE -ne 0) {
    throw "Full-panel aggregation failed."
}

$elapsedFinal = (Get-Date) - $panelStarted
Write-Host "====================================================================="
Write-Host "ENTRY V1 FULL PANEL COMPLETE"
Write-Host "processed=$totalStages/$totalStages (100.0%)"
Write-Host "elapsed=$(Format-Duration -Value $elapsedFinal) ETA=00:00"
Write-Host "Summary: $(Join-Path $panelOutput 'panel_summary.md')"
Write-Host "Asset matrix: $(Join-Path $panelOutput 'panel_asset_summary.csv')"
Write-Host "Transfer matrix: $(Join-Path $panelOutput 'panel_pipeline_transfer.csv')"
Write-Host "Context matrix: $(Join-Path $panelOutput 'panel_context_transfer.csv')"
Write-Host "====================================================================="
