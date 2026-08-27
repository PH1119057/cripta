param(
    [string[]]$Symbols = @(
        "BTCUSDT",
        "ETHUSDT",
        "XRPUSDT",
        "PEPEUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "ADAUSDT"
    ),
    [int]$Days = 90,
    [string]$LatestTradeDay = "2026-08-15",
    [string]$Endpoint = "https://api.bybit.kz",
    [int]$ReserveFreeSpaceGB = 10,
    [switch]$SkipOrderbook
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$p30Script = Join-Path $PSScriptRoot "research_entry_90d_windows.ps1"
if (-not (Test-Path $p30Script)) {
    throw "P30 research script not found: $p30Script"
}

$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curl) {
    throw "curl.exe was not found. Windows 10/11 normally includes it."
}

$tradeDay = [DateTime]::ParseExact(
    $LatestTradeDay,
    "yyyy-MM-dd",
    [Globalization.CultureInfo]::InvariantCulture
)
$evaluationEndDay = $tradeDay.AddDays(1)
$evaluationStartDay = $evaluationEndDay.AddDays(-$Days)
$period = $evaluationStartDay.ToString("yyyyMMdd") + "_" + $evaluationEndDay.ToString("yyyyMMdd")
$validationBase = Join-Path $root "reports\cross_asset_validation"
New-Item -ItemType Directory -Force -Path $validationBase | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $validationBase "P43_prefetch_${period}_${stamp}.log"
$statusPath = Join-Path $validationBase "P43_prefetch_${period}_${stamp}.json"

function Get-FreeBytes {
    $driveRoot = [System.IO.Path]::GetPathRoot($root)
    $drive = [System.IO.DriveInfo]::new($driveRoot)
    return [int64]$drive.AvailableFreeSpace
}

function Format-Bytes([int64]$Bytes) {
    if ($Bytes -ge 1TB) { return ("{0:N2} TiB" -f ($Bytes / 1TB)) }
    if ($Bytes -ge 1GB) { return ("{0:N2} GiB" -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ("{0:N2} MiB" -f ($Bytes / 1MB)) }
    return ("$Bytes bytes")
}

function Get-ContentLength([string]$Url) {
    try {
        $response = Invoke-WebRequest `
            -Uri $Url `
            -Method Head `
            -UseBasicParsing `
            -TimeoutSec 30
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) {
            return $null
        }
        $raw = $response.Headers["Content-Length"]
        if ($null -eq $raw) {
            return [int64]0
        }
        [int64]$length = 0
        if ([int64]::TryParse([string]$raw, [ref]$length)) {
            return $length
        }
        return [int64]0
    }
    catch {
        return $null
    }
}

function Get-OrderbookPlan([string]$Symbol, [string]$CacheDir) {
    $rows = @()
    $day = $evaluationStartDay
    while ($day -le $tradeDay) {
        $dayText = $day.ToString("yyyy-MM-dd")
        $selected = $null
        foreach ($depth in @(200, 500, 1000)) {
            $filename = "${dayText}_${Symbol}_ob${depth}.data.zip"
            $url = "https://quote-saver.bycsi.com/orderbook/linear/$Symbol/$filename"
            $length = Get-ContentLength $url
            if ($null -ne $length) {
                $selected = [pscustomobject]@{
                    day = $dayText
                    depth = $depth
                    url = $url
                    filename = $filename
                    expected_bytes = [int64]$length
                    destination = (Join-Path $CacheDir $filename)
                    status = "planned"
                }
                break
            }
        }
        if ($null -eq $selected) {
            $selected = [pscustomobject]@{
                day = $dayText
                depth = 0
                url = ""
                filename = ""
                expected_bytes = [int64]0
                destination = ""
                status = "missing"
            }
        }
        $rows += $selected
        $day = $day.AddDays(1)
    }
    return $rows
}

function Download-OrderbookArchive($PlanRow) {
    $destination = [string]$PlanRow.destination
    $expected = [int64]$PlanRow.expected_bytes
    if (-not $destination) {
        return "missing"
    }

    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $part = "$destination.part"

    if (Test-Path $destination) {
        $current = (Get-Item $destination).Length
        if ($expected -le 0 -or $current -eq $expected) {
            return "cached"
        }
        if (Test-Path $part) {
            Remove-Item $part -Force
        }
        Move-Item $destination $part -Force
    }

    $curlArgs = @(
        "--location",
        "--fail",
        "--retry", "5",
        "--retry-delay", "3",
        "--connect-timeout", "20",
        "--continue-at", "-",
        "--progress-bar",
        "--output", $part,
        [string]$PlanRow.url
    )
    & $curl.Source @curlArgs
    $code = $LASTEXITCODE

    if ($code -eq 33) {
        Write-Host "  server rejected resume; retry from byte 0"
        Remove-Item $part -Force -ErrorAction SilentlyContinue
        $freshArgs = @(
            "--location",
            "--fail",
            "--retry", "5",
            "--retry-delay", "3",
            "--connect-timeout", "20",
            "--progress-bar",
            "--output", $part,
            [string]$PlanRow.url
        )
        & $curl.Source @freshArgs
        $code = $LASTEXITCODE
    }

    if ($code -ne 0) {
        throw "curl failed with exit code $code for $($PlanRow.url)"
    }
    if (-not (Test-Path $part)) {
        throw "download completed without a file: $part"
    }
    if ($expected -gt 0 -and (Get-Item $part).Length -ne $expected) {
        throw "archive size mismatch for $($PlanRow.filename): got $((Get-Item $part).Length), expected $expected"
    }
    Move-Item $part $destination -Force
    return "downloaded"
}

$portfolioStatus = @()
Start-Transcript -Path $logPath -Force | Out-Null
try {
    Write-Host "============================================================="
    Write-Host "P43 MULTI-ASSET 90D PREFETCH"
    Write-Host "Frozen evaluation: $($evaluationStartDay.ToString('yyyy-MM-dd')) .. $($evaluationEndDay.ToString('yyyy-MM-dd')) UTC"
    Write-Host "Latest raw trade day: $LatestTradeDay"
    Write-Host "New symbols: $($Symbols -join ', ')"
    Write-Host "UNIUSDT and LINKUSDT are intentionally not re-downloaded by default."
    Write-Host "============================================================="

    foreach ($rawSymbol in $Symbols) {
        $symbol = $rawSymbol.Trim().ToUpperInvariant()
        if (-not $symbol) { continue }

        $validationRoot = Join-Path $validationBase "${symbol}_${period}"
        $p30Dir = Join-Path $validationRoot "p30"
        $datasetDir = Join-Path $p30Dir "dataset"
        $manifestPath = Join-Path $datasetDir "dataset_manifest.json"
        $orderbookCache = Join-Path $datasetDir "orderbook_cache"
        $planPath = Join-Path $validationRoot "orderbook_prefetch_plan.csv"

        $item = [ordered]@{
            symbol = $symbol
            validation_root = $validationRoot
            dataset_manifest = $manifestPath
            p30_dataset = "pending"
            orderbook_days_planned = 0
            orderbook_days_missing = 0
            orderbook_bytes_planned = 0
            orderbook_cache = $orderbookCache
            orderbook = if ($SkipOrderbook) { "skipped" } else { "pending" }
            error = ""
        }

        Write-Host ""
        Write-Host "#############################################################"
        Write-Host "P43 SYMBOL: $symbol"
        Write-Host "#############################################################"

        try {
            if (Test-Path $manifestPath) {
                Write-Host "P30 frozen dataset already exists: $datasetDir"
                $item.p30_dataset = "cached"
            }
            else {
                & $p30Script `
                    -Symbol $symbol `
                    -Days $Days `
                    -Endpoint $Endpoint `
                    -LatestTradeDay $LatestTradeDay `
                    -DatasetDir $datasetDir `
                    -OutputDir $p30Dir
                if ($LASTEXITCODE -ne 0) {
                    throw "P30 dataset preparation failed for $symbol"
                }
                if (-not (Test-Path $manifestPath)) {
                    throw "P30 completed but dataset manifest is missing: $manifestPath"
                }
                $item.p30_dataset = "downloaded"
            }

            if (-not $SkipOrderbook) {
                New-Item -ItemType Directory -Force -Path $orderbookCache | Out-Null
                Write-Host "Preflight 90 daily orderbook archives for $symbol ..."
                $plan = @(Get-OrderbookPlan $symbol $orderbookCache)
                $plan | Export-Csv -Path $planPath -NoTypeInformation -Encoding UTF8

                $available = @($plan | Where-Object { $_.status -ne "missing" })
                $missing = @($plan | Where-Object { $_.status -eq "missing" })
                [int64]$plannedBytes = ($available | Measure-Object -Property expected_bytes -Sum).Sum
                $item.orderbook_days_planned = $available.Count
                $item.orderbook_days_missing = $missing.Count
                $item.orderbook_bytes_planned = $plannedBytes

                Write-Host "Orderbook available: $($available.Count)/$Days days"
                if ($missing.Count -gt 0) {
                    Write-Warning "Missing orderbook days: $((($missing | Select-Object -ExpandProperty day) -join ', '))"
                }
                if ($plannedBytes -gt 0) {
                    Write-Host "Orderbook expected download size: $(Format-Bytes $plannedBytes)"
                }

                [int64]$reserve = [int64]$ReserveFreeSpaceGB * 1GB
                [int64]$free = Get-FreeBytes
                if ($plannedBytes -gt 0 -and $free -lt ($plannedBytes + $reserve)) {
                    throw "Not enough free space for $symbol orderbook cache. Free=$(Format-Bytes $free), planned=$(Format-Bytes $plannedBytes), reserve=${ReserveFreeSpaceGB}GB"
                }

                $index = 0
                foreach ($row in $available) {
                    $index += 1
                    Write-Host "Orderbook $symbol $index/$($available.Count): $($row.day) ob$($row.depth)"
                    $row.status = Download-OrderbookArchive $row
                    $plan | Export-Csv -Path $planPath -NoTypeInformation -Encoding UTF8
                }
                $item.orderbook = if ($missing.Count -eq 0) { "complete" } else { "complete_with_missing_days" }
            }
        }
        catch {
            $item.error = $_.Exception.Message
            if ($item.p30_dataset -eq "pending") { $item.p30_dataset = "failed" }
            if (-not $SkipOrderbook -and $item.orderbook -eq "pending") { $item.orderbook = "failed" }
            Write-Error -ErrorAction Continue "P43 $symbol failed: $($item.error)"
        }

        $portfolioStatus += [pscustomobject]$item
        $portfolioStatus | ConvertTo-Json -Depth 6 | Set-Content -Path $statusPath -Encoding UTF8
    }

    Write-Host ""
    Write-Host "============================================================="
    Write-Host "P43 PREFETCH FINISHED"
    Write-Host "Status: $statusPath"
    Write-Host "Log:    $logPath"
    Write-Host ""
    $portfolioStatus | Format-Table symbol, p30_dataset, orderbook_days_planned, orderbook_days_missing, orderbook, error -AutoSize
    Write-Host "============================================================="
}
finally {
    Stop-Transcript | Out-Null
}
