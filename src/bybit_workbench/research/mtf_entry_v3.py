from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import ssl
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

import certifi

from bybit_workbench.domain.models import Candle
from bybit_workbench.research.mtf_entry import (
    Direction,
    _decimal_json,
    _fingerprint,
    _metrics_for_signal,
    _server_time_ms,
    _write_candles,
    download_klines,
)
from bybit_workbench.research.mtf_entry_v2 import (
    EntryResearchV2Config,
    _zone_gap_percent,
    run_mtf15_regime_research,
)
from bybit_workbench.strategies.indicators import true_ranges, wilder_atr

HourlyContext = Literal["Long", "Short", "Neutral"]
HourlyAlignment = Literal["aligned", "opposed", "neutral"]


@dataclass(frozen=True, slots=True)
class EntryResearchV3Config:
    symbol: str = "UNIUSDT"
    endpoint: str = "https://api.bybit.kz"
    days: int = 90
    warmup_days: int = 14
    five_minute_lookback: int = 130
    fifteen_minute_lookback: int = 130
    hourly_lookback: int = 130
    atr_period: int = 200
    zone_half_width_atr: Decimal = Decimal("0.5")
    confluence_max_gap_percent: Decimal = Decimal("0.25")
    cooldown_minutes: int = 30
    horizons_minutes: tuple[int, ...] = (30, 60, 120, 240, 360)
    shock_atr_period: int = 20
    shock_atr_multiple: Decimal = Decimal("3.0")
    embargo_minutes_after_shock: int = 60
    flow_windows_minutes: tuple[int, ...] = (1, 5, 15, 30)
    archive_root: str = "https://public.bybit.com/trading"
    archive_probe_days: int = 14
    include_open_interest: bool = True
    latest_trade_day_override: date | None = None

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.days <= 0 or self.warmup_days < 0:
            raise ValueError("days must be positive and warmup_days cannot be negative")
        if self.days % 30:
            raise ValueError("days must be divisible by 30 for stability slices")
        if min(
            self.five_minute_lookback,
            self.fifteen_minute_lookback,
            self.hourly_lookback,
        ) <= 0:
            raise ValueError("lookbacks must be positive")
        if self.atr_period <= 1:
            raise ValueError("atr_period must be greater than one")
        if self.zone_half_width_atr <= 0:
            raise ValueError("zone_half_width_atr must be positive")
        if self.confluence_max_gap_percent < 0:
            raise ValueError("confluence_max_gap_percent cannot be negative")
        if self.cooldown_minutes < 0 or self.cooldown_minutes % 5:
            raise ValueError("cooldown_minutes must be a non-negative multiple of five")
        if self.embargo_minutes_after_shock < 0:
            raise ValueError("embargo_minutes_after_shock cannot be negative")
        if self.archive_probe_days <= 0:
            raise ValueError("archive_probe_days must be positive")
        if not self.flow_windows_minutes or any(value <= 0 for value in self.flow_windows_minutes):
            raise ValueError("flow windows must be positive")


@dataclass(frozen=True, slots=True)
class ZoneV3:
    timeframe: str
    observed_at: datetime
    range_high: Decimal
    range_low: Decimal
    atr: Decimal
    resistance_top: Decimal
    resistance_bottom: Decimal
    support_top: Decimal
    support_bottom: Decimal
    effective_lookback: int
    regime_reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class FlowBucket:
    opened_at: datetime
    buy_notional: Decimal = Decimal("0")
    sell_notional: Decimal = Decimal("0")
    buy_size: Decimal = Decimal("0")
    sell_size: Decimal = Decimal("0")
    buy_trades: int = 0
    sell_trades: int = 0


@dataclass(frozen=True, slots=True)
class EntrySignalV3:
    symbol: str
    direction: Direction
    entry_at: datetime
    entry_price: Decimal
    hourly_context: HourlyContext
    hourly_return_percent: Decimal
    hourly_alignment: HourlyAlignment
    fifteen_zone_low: Decimal
    fifteen_zone_high: Decimal
    five_zone_low: Decimal
    five_zone_high: Decimal
    zone_gap_percent: Decimal
    hourly_effective_lookback: int
    fifteen_effective_lookback: int
    five_effective_lookback: int
    hourly_regime_reset_at: datetime | None
    fifteen_regime_reset_at: datetime | None
    five_regime_reset_at: datetime | None
    outcome_metrics: dict[str, Decimal | int | str | None]
    flow_metrics: dict[str, Decimal | int | str | None] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EntryResearchV3Result:
    config: EntryResearchV3Config
    signals: tuple[EntrySignalV3, ...]
    summary: dict[str, Any]
    five_minute_fingerprint: str
    fifteen_minute_fingerprint: str
    hourly_fingerprint: str


_HTTPS_CONTEXT = ssl.create_default_context(cafile=certifi.where())


def _http_request(url: str, *, method: str = "GET", timeout: float = 30.0) -> Any:
    request = urllib.request.Request(
        url,
        method=method,
        headers={"User-Agent": "BybitStrategyWorkbench/0.8.5 research"},
    )
    return urllib.request.urlopen(request, timeout=timeout, context=_HTTPS_CONTEXT)


def _archive_url(root: str, symbol: str, day: date) -> str:
    normalized_root = root.rstrip("/")
    day_text = day.isoformat()
    return f"{normalized_root}/{symbol}/{symbol}{day_text}.csv.gz"


def _archive_exists(url: str) -> bool:
    try:
        with _http_request(url, method="HEAD", timeout=15.0) as response:
            return int(response.status) == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        if exc.code not in {403, 405}:
            raise
    try:
        with _http_request(url, method="GET", timeout=15.0) as response:
            return int(response.status) == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


def discover_latest_archive_day(config: EntryResearchV3Config, *, server_now: datetime) -> date:
    # Never use the current UTC day: a daily tape can still be incomplete.
    candidate = server_now.date() - timedelta(days=1)
    for offset in range(config.archive_probe_days):
        day = candidate - timedelta(days=offset)
        url = _archive_url(config.archive_root, config.symbol, day)
        print(f"Probe public trade archive: {day.isoformat()}")
        if _archive_exists(url):
            return day
    raise FileNotFoundError(
        f"no complete public trade archive found for {config.symbol} in the last "
        f"{config.archive_probe_days} days"
    )


def _download_with_resume(url: str, destination: Path, *, attempts: int = 3) -> None:
    if destination.exists() and destination.stat().st_size > 0:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, attempts + 1):
        try:
            with _http_request(url, timeout=120.0) as response, temporary.open("wb") as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            temporary.replace(destination)
            return
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt >= attempts:
                raise
            time.sleep(float(attempt))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_archive_timestamp(raw: str) -> datetime:
    value = Decimal(raw.strip())
    absolute = abs(value)
    if absolute >= Decimal("1e18"):
        seconds = value / Decimal("1e9")
    elif absolute >= Decimal("1e15"):
        seconds = value / Decimal("1e6")
    elif absolute >= Decimal("1e11"):
        seconds = value / Decimal("1e3")
    else:
        seconds = value
    return datetime.fromtimestamp(float(seconds), UTC)


def _flow_bucket_update(
    current: FlowBucket | None,
    *,
    minute: datetime,
    side: str,
    size: Decimal,
    price: Decimal,
) -> FlowBucket:
    base = current or FlowBucket(opened_at=minute)
    notional = size * price
    if side == "Buy":
        return FlowBucket(
            opened_at=minute,
            buy_notional=base.buy_notional + notional,
            sell_notional=base.sell_notional,
            buy_size=base.buy_size + size,
            sell_size=base.sell_size,
            buy_trades=base.buy_trades + 1,
            sell_trades=base.sell_trades,
        )
    if side == "Sell":
        return FlowBucket(
            opened_at=minute,
            buy_notional=base.buy_notional,
            sell_notional=base.sell_notional + notional,
            buy_size=base.buy_size,
            sell_size=base.sell_size + size,
            buy_trades=base.buy_trades,
            sell_trades=base.sell_trades + 1,
        )
    return base


def aggregate_public_trade_archives(
    archive_paths: tuple[Path, ...],
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[FlowBucket, ...]:
    buckets: dict[datetime, FlowBucket] = {}
    for path in archive_paths:
        print(f"Aggregate taker flow: {path.name}")
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"trade archive has no header: {path}")
            names = {name.strip().lower(): name for name in reader.fieldnames if name}
            required = {"timestamp", "side", "size", "price"}
            if not required.issubset(names):
                raise ValueError(
                    f"unsupported Bybit trade archive header in {path.name}: {reader.fieldnames}"
                )
            for row in reader:
                try:
                    traded_at = _parse_archive_timestamp(str(row[names["timestamp"]]))
                    if traded_at < start_at or traded_at >= end_at:
                        continue
                    side = str(row[names["side"]]).strip().title()
                    size = Decimal(str(row[names["size"]]).strip())
                    price = Decimal(str(row[names["price"]]).strip())
                except (InvalidOperation, ValueError, TypeError) as exc:
                    raise ValueError(f"invalid trade row in {path.name}: {row}") from exc
                minute = traded_at.replace(second=0, microsecond=0)
                buckets[minute] = _flow_bucket_update(
                    buckets.get(minute),
                    minute=minute,
                    side=side,
                    size=size,
                    price=price,
                )
    return tuple(buckets[key] for key in sorted(buckets))


def _write_flow(path: Path, buckets: tuple[FlowBucket, ...]) -> None:
    fields = [
        "opened_at",
        "buy_notional",
        "sell_notional",
        "delta_notional",
        "buy_size",
        "sell_size",
        "buy_trades",
        "sell_trades",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for bucket in buckets:
            writer.writerow(
                {
                    "opened_at": bucket.opened_at.isoformat(),
                    "buy_notional": bucket.buy_notional,
                    "sell_notional": bucket.sell_notional,
                    "delta_notional": bucket.buy_notional - bucket.sell_notional,
                    "buy_size": bucket.buy_size,
                    "sell_size": bucket.sell_size,
                    "buy_trades": bucket.buy_trades,
                    "sell_trades": bucket.sell_trades,
                }
            )


def _read_flow(path: Path) -> tuple[FlowBucket, ...]:
    items: list[FlowBucket] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            items.append(
                FlowBucket(
                    opened_at=datetime.fromisoformat(str(row["opened_at"])),
                    buy_notional=Decimal(str(row["buy_notional"])),
                    sell_notional=Decimal(str(row["sell_notional"])),
                    buy_size=Decimal(str(row["buy_size"])),
                    sell_size=Decimal(str(row["sell_size"])),
                    buy_trades=int(str(row["buy_trades"])),
                    sell_trades=int(str(row["sell_trades"])),
                )
            )
    return tuple(items)


def _public_json(endpoint: str, path: str, params: dict[str, str | int]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    url = f"{endpoint.rstrip('/')}{path}?{query}"
    with _http_request(url, timeout=30.0) as response:
        payload = cast(dict[str, Any], json.loads(response.read().decode("utf-8")))
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(
            f"Bybit public API failed: {payload.get('retCode')} {payload.get('retMsg')}"
        )
    return payload


def download_open_interest(
    config: EntryResearchV3Config,
    *,
    start_at: datetime,
    end_at: datetime,
) -> tuple[tuple[datetime, Decimal], ...]:
    rows: dict[int, Decimal] = {}
    cursor = ""
    while True:
        params: dict[str, str | int] = {
            "category": "linear",
            "symbol": config.symbol,
            "intervalTime": "5min",
            "startTime": int(start_at.timestamp() * 1000),
            "endTime": int(end_at.timestamp() * 1000) - 1,
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        payload = _public_json(config.endpoint, "/v5/market/open-interest", params)
        result = payload.get("result") or {}
        for item in result.get("list") or []:
            timestamp = int(str(item["timestamp"]))
            rows[timestamp] = Decimal(str(item["openInterest"]))
        next_cursor = str(result.get("nextPageCursor") or "")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(0.02)
    return tuple(
        (datetime.fromtimestamp(timestamp / 1000, UTC), rows[timestamp])
        for timestamp in sorted(rows)
    )


def _write_open_interest(path: Path, rows: tuple[tuple[datetime, Decimal], ...]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "open_interest"])
        writer.writeheader()
        for timestamp, value in rows:
            writer.writerow({"timestamp": timestamp.isoformat(), "open_interest": value})


def _precompute_post_shock_zones(
    candles: tuple[Candle, ...],
    *,
    timeframe: str,
    lookback: int,
    atr_period: int,
    width_atr: Decimal,
    shock_atr_period: int,
    shock_atr_multiple: Decimal,
    minimum_regime_bars: int,
) -> tuple[ZoneV3 | None, ...]:
    """Causal zones that forget the shock candle and everything before it."""

    count = len(candles)
    zones: list[ZoneV3 | None] = [None] * (count + 1)
    if not candles:
        return tuple(zones)
    ranges = true_ranges(candles)
    atr_values = wilder_atr(candles, atr_period)
    shock_flags = [False] * count
    if shock_atr_period > 0 and count > shock_atr_period:
        rolling = sum(ranges[:shock_atr_period], Decimal("0"))
        for index in range(shock_atr_period, count):
            baseline = rolling / Decimal(shock_atr_period)
            shock_flags[index] = baseline > 0 and ranges[index] >= shock_atr_multiple * baseline
            rolling += ranges[index] - ranges[index - shock_atr_period]

    minimum_history = max(lookback, atr_period)
    for history_len in range(minimum_history, count + 1):
        last_index = history_len - 1
        atr = atr_values[last_index]
        if atr is None or atr <= 0:
            continue
        window_start = history_len - lookback
        reset_index: int | None = None
        for index in range(last_index, max(window_start, shock_atr_period) - 1, -1):
            if shock_flags[index]:
                reset_index = index
                break
        selected_start = window_start
        reset_at: datetime | None = None
        if reset_index is not None:
            # The shock itself belongs to the old transition, not the new support/resistance sample.
            selected_start = reset_index + 1
            if history_len - selected_start < minimum_regime_bars:
                continue
            reset_at = candles[reset_index].closed_at
        selected = candles[selected_start:history_len]
        if not selected:
            continue
        range_high = max(item.high for item in selected)
        range_low = min(item.low for item in selected)
        width = width_atr * atr
        support_top = range_low + width
        support_bottom = range_low - width
        resistance_top = range_high + width
        resistance_bottom = range_high - width
        if support_top >= resistance_bottom:
            continue
        zones[history_len] = ZoneV3(
            timeframe=timeframe,
            observed_at=candles[last_index].closed_at,
            range_high=range_high,
            range_low=range_low,
            atr=atr,
            resistance_top=resistance_top,
            resistance_bottom=resistance_bottom,
            support_top=support_top,
            support_bottom=support_bottom,
            effective_lookback=len(selected),
            regime_reset_at=reset_at,
        )
    return tuple(zones)


def _hourly_context_from_zone(
    hours: tuple[Candle, ...], hourly_end: int, zone: ZoneV3 | None
) -> tuple[HourlyContext, Decimal, int, datetime | None]:
    if zone is None or zone.effective_lookback < 2:
        return "Neutral", Decimal("0"), 0, None
    selected = hours[hourly_end - zone.effective_lookback : hourly_end]
    if len(selected) < 2 or selected[0].close <= 0:
        return "Neutral", Decimal("0"), len(selected), zone.regime_reset_at
    return_percent = (selected[-1].close / selected[0].close - Decimal("1")) * Decimal("100")
    if return_percent > 0:
        context: HourlyContext = "Long"
    elif return_percent < 0:
        context = "Short"
    else:
        context = "Neutral"
    return context, return_percent, len(selected), zone.regime_reset_at


def run_local_mtf_research(
    five_minute: tuple[Candle, ...],
    fifteen_minute: tuple[Candle, ...],
    hourly: tuple[Candle, ...],
    config: EntryResearchV3Config,
    *,
    evaluation_start: datetime,
) -> EntryResearchV3Result:
    symbol = config.symbol.upper()
    five = tuple(sorted(five_minute, key=lambda item: item.opened_at))
    fifteen = tuple(sorted(fifteen_minute, key=lambda item: item.opened_at))
    hours = tuple(sorted(hourly, key=lambda item: item.opened_at))
    if any(item.symbol != symbol or item.timeframe != "5" for item in five):
        raise ValueError("five-minute dataset does not match symbol/timeframe")
    if any(item.symbol != symbol or item.timeframe != "15" for item in fifteen):
        raise ValueError("fifteen-minute dataset does not match symbol/timeframe")
    if any(item.symbol != symbol or item.timeframe != "60" for item in hours):
        raise ValueError("hourly dataset does not match symbol/timeframe")
    if not five or not fifteen or not hours:
        raise ValueError("5m, 15m and 60m datasets are required")

    five_embargo = max(1, config.embargo_minutes_after_shock // 5)
    fifteen_embargo = max(1, config.embargo_minutes_after_shock // 15)
    hourly_embargo = max(1, config.embargo_minutes_after_shock // 60)
    five_zones = _precompute_post_shock_zones(
        five,
        timeframe="5",
        lookback=config.five_minute_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=five_embargo,
    )
    fifteen_zones = _precompute_post_shock_zones(
        fifteen,
        timeframe="15",
        lookback=config.fifteen_minute_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=fifteen_embargo,
    )
    hourly_zones = _precompute_post_shock_zones(
        hours,
        timeframe="60",
        lookback=config.hourly_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=hourly_embargo,
    )

    signals: list[EntrySignalV3] = []
    fifteen_end = 0
    hourly_end = 0
    next_allowed_index = 0
    minimum_five = max(config.five_minute_lookback, config.atr_period)
    minimum_fifteen = max(config.fifteen_minute_lookback, config.atr_period)
    minimum_hourly = max(config.hourly_lookback, config.atr_period)
    cooldown_bars = config.cooldown_minutes // 5

    for index, bar in enumerate(five):
        if index < minimum_five or bar.opened_at < evaluation_start:
            continue
        if index < next_allowed_index:
            continue
        while fifteen_end < len(fifteen) and fifteen[fifteen_end].closed_at <= bar.opened_at:
            fifteen_end += 1
        while hourly_end < len(hours) and hours[hourly_end].closed_at <= bar.opened_at:
            hourly_end += 1
        if fifteen_end < minimum_fifteen or hourly_end < minimum_hourly:
            continue

        five_zone = five_zones[index]
        fifteen_zone = fifteen_zones[fifteen_end]
        if five_zone is None or fifteen_zone is None:
            continue
        hourly_zone = hourly_zones[hourly_end]
        hourly_context, hourly_return, hourly_effective, hourly_reset = _hourly_context_from_zone(
            hours, hourly_end, hourly_zone
        )

        reference = bar.open
        long_gap = _zone_gap_percent(
            fifteen_zone.support_bottom,
            fifteen_zone.support_top,
            five_zone.support_bottom,
            five_zone.support_top,
            reference,
        )
        short_gap = _zone_gap_percent(
            fifteen_zone.resistance_bottom,
            fifteen_zone.resistance_top,
            five_zone.resistance_bottom,
            five_zone.resistance_top,
            reference,
        )
        long_entry = five_zone.support_top
        short_entry = five_zone.resistance_bottom
        long_ok = long_gap <= config.confluence_max_gap_percent and bar.low <= long_entry
        short_ok = short_gap <= config.confluence_max_gap_percent and bar.high >= short_entry
        if long_ok == short_ok:
            continue

        if long_ok:
            direction: Direction = "Long"
            entry = long_entry
            gap = long_gap
            fifteen_low, fifteen_high = fifteen_zone.support_bottom, fifteen_zone.support_top
            five_low, five_high = five_zone.support_bottom, five_zone.support_top
        else:
            direction = "Short"
            entry = short_entry
            gap = short_gap
            fifteen_low, fifteen_high = (
                fifteen_zone.resistance_bottom,
                fifteen_zone.resistance_top,
            )
            five_low, five_high = five_zone.resistance_bottom, five_zone.resistance_top

        if hourly_context == "Neutral":
            alignment: HourlyAlignment = "neutral"
        elif hourly_context == direction:
            alignment = "aligned"
        else:
            alignment = "opposed"

        metrics = _metrics_for_signal(direction, entry, five, index, config.horizons_minutes)
        signals.append(
            EntrySignalV3(
                symbol=symbol,
                direction=direction,
                entry_at=bar.opened_at,
                entry_price=entry,
                hourly_context=hourly_context,
                hourly_return_percent=hourly_return,
                hourly_alignment=alignment,
                fifteen_zone_low=fifteen_low,
                fifteen_zone_high=fifteen_high,
                five_zone_low=five_low,
                five_zone_high=five_high,
                zone_gap_percent=gap,
                hourly_effective_lookback=hourly_effective,
                fifteen_effective_lookback=fifteen_zone.effective_lookback,
                five_effective_lookback=five_zone.effective_lookback,
                hourly_regime_reset_at=hourly_reset,
                fifteen_regime_reset_at=fifteen_zone.regime_reset_at,
                five_regime_reset_at=five_zone.regime_reset_at,
                outcome_metrics=metrics,
            )
        )
        next_allowed_index = index + cooldown_bars

    signal_tuple = tuple(signals)
    return EntryResearchV3Result(
        config=config,
        signals=signal_tuple,
        summary=_summary_signals(signal_tuple, config=config),
        five_minute_fingerprint=_fingerprint(five),
        fifteen_minute_fingerprint=_fingerprint(fifteen),
        hourly_fingerprint=_fingerprint(hours),
    )


def _percent(count: int, total: int) -> float:
    return 0.0 if total == 0 else round(count * 100.0 / total, 2)


def _summary_signals(
    signals: tuple[EntrySignalV3, ...], *, config: EntryResearchV3Config
) -> dict[str, Any]:
    total = len(signals)
    result: dict[str, Any] = {
        "signals": total,
        "signals_per_day": round(total / config.days, 3),
        "long": sum(signal.direction == "Long" for signal in signals),
        "short": sum(signal.direction == "Short" for signal in signals),
        "hourly_aligned": sum(signal.hourly_alignment == "aligned" for signal in signals),
        "hourly_opposed": sum(signal.hourly_alignment == "opposed" for signal in signals),
        "hourly_neutral": sum(signal.hourly_alignment == "neutral" for signal in signals),
    }
    if not signals:
        return result
    result["median_zone_gap_percent"] = round(
        float(statistics.median(signal.zone_gap_percent for signal in signals)), 5
    )
    result["median_abs_hourly_return_percent"] = round(
        float(statistics.median(abs(signal.hourly_return_percent) for signal in signals)), 4
    )
    for horizon in config.horizons_minutes:
        mfe = [Decimal(str(signal.outcome_metrics[f"mfe_{horizon}m_pct"])) for signal in signals]
        mae = [Decimal(str(signal.outcome_metrics[f"mae_{horizon}m_pct"])) for signal in signals]
        result[f"median_mfe_{horizon}m_pct"] = round(float(statistics.median(mfe)), 4)
        result[f"median_mae_{horizon}m_pct"] = round(float(statistics.median(mae)), 4)
    for target in ("0_5", "1", "2", "3", "5"):
        key = f"hit_plus_{target}_pct"
        hits = 0
        for signal in signals:
            value = signal.outcome_metrics[key]
            if not isinstance(value, int):
                raise TypeError(f"{key} must be an int")
            hits += value
        result[f"hit_plus_{target}_pct_rate"] = _percent(hits, total)
    for key in ("first_0_5_vs_0_5", "first_0_5_vs_1_0"):
        names = ("favorable_first", "adverse_first", "ambiguous_same_bar", "neither")
        counts = {name: 0 for name in names}
        for signal in signals:
            counts[str(signal.outcome_metrics[key])] += 1
        result[key] = {
            name: {"count": count, "percent": _percent(count, total)}
            for name, count in counts.items()
        }
    return result


def _flow_metrics_for_signal(
    signal: EntrySignalV3,
    buckets_by_minute: dict[datetime, FlowBucket],
    windows: tuple[int, ...],
) -> dict[str, Decimal | int | str | None]:
    metrics: dict[str, Decimal | int | str | None] = {}
    for window in windows:
        buy = Decimal("0")
        sell = Decimal("0")
        buy_trades = 0
        sell_trades = 0
        end = signal.entry_at.replace(second=0, microsecond=0)
        for offset in range(window):
            minute = end - timedelta(minutes=offset + 1)
            bucket = buckets_by_minute.get(minute)
            if bucket is None:
                continue
            buy += bucket.buy_notional
            sell += bucket.sell_notional
            buy_trades += bucket.buy_trades
            sell_trades += bucket.sell_trades
        total = buy + sell
        delta = buy - sell
        delta_pct = Decimal("0") if total <= 0 else delta / total * Decimal("100")
        directional = delta_pct if signal.direction == "Long" else -delta_pct
        buy_share = Decimal("0") if total <= 0 else buy / total * Decimal("100")
        metrics[f"flow_{window}m_buy_notional"] = buy
        metrics[f"flow_{window}m_sell_notional"] = sell
        metrics[f"flow_{window}m_delta_notional"] = delta
        metrics[f"flow_{window}m_delta_pct"] = delta_pct
        metrics[f"flow_{window}m_directional_delta_pct"] = directional
        metrics[f"flow_{window}m_buy_share_pct"] = buy_share
        metrics[f"flow_{window}m_buy_trades"] = buy_trades
        metrics[f"flow_{window}m_sell_trades"] = sell_trades
    return metrics


def enrich_with_flow(
    result: EntryResearchV3Result, buckets: tuple[FlowBucket, ...]
) -> EntryResearchV3Result:
    mapping = {bucket.opened_at: bucket for bucket in buckets}
    signals = tuple(
        replace(
            signal,
            flow_metrics=_flow_metrics_for_signal(
                signal, mapping, result.config.flow_windows_minutes
            ),
        )
        for signal in result.signals
    )
    summary = dict(result.summary)
    summary["flow_5m_quartiles"] = _flow_quartile_summary(signals, result.config)
    return EntryResearchV3Result(
        config=result.config,
        signals=signals,
        summary=summary,
        five_minute_fingerprint=result.five_minute_fingerprint,
        fifteen_minute_fingerprint=result.fifteen_minute_fingerprint,
        hourly_fingerprint=result.hourly_fingerprint,
    )


def _flow_quartile_summary(
    signals: tuple[EntrySignalV3, ...], config: EntryResearchV3Config
) -> list[dict[str, Any]]:
    usable = [
        signal
        for signal in signals
        if isinstance(signal.flow_metrics.get("flow_5m_directional_delta_pct"), Decimal)
    ]
    ordered = sorted(
        usable,
        key=lambda signal: Decimal(str(signal.flow_metrics["flow_5m_directional_delta_pct"])),
    )
    if not ordered:
        return []
    groups: list[dict[str, Any]] = []
    for quartile in range(4):
        start = len(ordered) * quartile // 4
        end = len(ordered) * (quartile + 1) // 4
        subset = tuple(ordered[start:end])
        if not subset:
            continue
        mini = _summary_signals(subset, config=config)
        values = [
            Decimal(str(item.flow_metrics["flow_5m_directional_delta_pct"]))
            for item in subset
        ]
        groups.append(
            {
                "quartile": quartile + 1,
                "signals": len(subset),
                "min_directional_delta_pct": float(min(values)),
                "median_directional_delta_pct": float(statistics.median(values)),
                "max_directional_delta_pct": float(max(values)),
                "hit_plus_0_5_pct_rate": mini.get("hit_plus_0_5_pct_rate", 0.0),
                "hit_plus_1_pct_rate": mini.get("hit_plus_1_pct_rate", 0.0),
                "first_0_5_vs_1_0": mini.get("first_0_5_vs_1_0", {}),
            }
        )
    return groups


def _slice_summary(
    signals: tuple[EntrySignalV3, ...],
    *,
    config: EntryResearchV3Config,
    evaluation_start: datetime,
) -> list[dict[str, Any]]:
    slices: list[dict[str, Any]] = []
    for index in range(config.days // 30):
        start = evaluation_start + timedelta(days=index * 30)
        end = start + timedelta(days=30)
        subset = tuple(signal for signal in signals if start <= signal.entry_at < end)
        slice_config = EntryResearchV3Config(**{**asdict(config), "days": 30})
        summary = _summary_signals(subset, config=slice_config)
        slices.append({"index": index + 1, "start": start, "end": end, "summary": summary})
    return slices


def _stability_summary(slices: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "signals_per_day",
        "hit_plus_0_5_pct_rate",
        "hit_plus_1_pct_rate",
    )
    result: dict[str, Any] = {}
    for key in keys:
        values = [float(item["summary"].get(key, 0.0)) for item in slices]
        if not values:
            continue
        result[key] = {
            "min": min(values),
            "max": max(values),
            "range": round(max(values) - min(values), 3),
            "mean": round(statistics.mean(values), 3),
        }
    favorable_values: list[float] = []
    for item in slices:
        nested = item["summary"].get("first_0_5_vs_1_0", {})
        favorable_values.append(float(nested.get("favorable_first", {}).get("percent", 0.0)))
    if favorable_values:
        result["first_0_5_vs_1_0_favorable_percent"] = {
            "min": min(favorable_values),
            "max": max(favorable_values),
            "range": round(max(favorable_values) - min(favorable_values), 3),
            "mean": round(statistics.mean(favorable_values), 3),
        }
    return result


def _write_v3_signals(path: Path, result: EntryResearchV3Result) -> None:
    outcome_names = [
        *(f"mfe_{value}m_pct" for value in result.config.horizons_minutes),
        *(f"mae_{value}m_pct" for value in result.config.horizons_minutes),
        "hit_plus_0_5_pct",
        "hit_plus_1_pct",
        "hit_plus_2_pct",
        "hit_plus_3_pct",
        "hit_plus_5_pct",
        "first_0_5_vs_0_5",
        "first_0_5_vs_1_0",
    ]
    flow_names: list[str] = []
    for window in result.config.flow_windows_minutes:
        flow_names.extend(
            [
                f"flow_{window}m_buy_notional",
                f"flow_{window}m_sell_notional",
                f"flow_{window}m_delta_notional",
                f"flow_{window}m_delta_pct",
                f"flow_{window}m_directional_delta_pct",
                f"flow_{window}m_buy_share_pct",
                f"flow_{window}m_buy_trades",
                f"flow_{window}m_sell_trades",
            ]
        )
    fields = [
        "symbol",
        "direction",
        "entry_at",
        "entry_price",
        "hourly_context",
        "hourly_return_percent",
        "hourly_alignment",
        "fifteen_zone_low",
        "fifteen_zone_high",
        "five_zone_low",
        "five_zone_high",
        "zone_gap_percent",
        "hourly_effective_lookback",
        "fifteen_effective_lookback",
        "five_effective_lookback",
        "hourly_regime_reset_at",
        "fifteen_regime_reset_at",
        "five_regime_reset_at",
        *outcome_names,
        *flow_names,
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for signal in result.signals:
            row: dict[str, Any] = {
                "symbol": signal.symbol,
                "direction": signal.direction,
                "entry_at": signal.entry_at.isoformat(),
                "entry_price": signal.entry_price,
                "hourly_context": signal.hourly_context,
                "hourly_return_percent": signal.hourly_return_percent,
                "hourly_alignment": signal.hourly_alignment,
                "fifteen_zone_low": signal.fifteen_zone_low,
                "fifteen_zone_high": signal.fifteen_zone_high,
                "five_zone_low": signal.five_zone_low,
                "five_zone_high": signal.five_zone_high,
                "zone_gap_percent": signal.zone_gap_percent,
                "hourly_effective_lookback": signal.hourly_effective_lookback,
                "fifteen_effective_lookback": signal.fifteen_effective_lookback,
                "five_effective_lookback": signal.five_effective_lookback,
                "hourly_regime_reset_at": (
                    signal.hourly_regime_reset_at.isoformat()
                    if signal.hourly_regime_reset_at
                    else ""
                ),
                "fifteen_regime_reset_at": (
                    signal.fifteen_regime_reset_at.isoformat()
                    if signal.fifteen_regime_reset_at
                    else ""
                ),
                "five_regime_reset_at": (
                    signal.five_regime_reset_at.isoformat() if signal.five_regime_reset_at else ""
                ),
            }
            row.update(signal.outcome_metrics)
            row.update(signal.flow_metrics)
            writer.writerow(row)


def _json_default(value: Any) -> Any:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return _decimal_json(value)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _required(row: dict[str, str | None], key: str) -> str:
    value = row.get(key)
    if value is None or value == "":
        raise ValueError(f"missing CSV field: {key}")
    return value


def _read_candles(path: Path, *, symbol: str, timeframe: str) -> tuple[Candle, ...]:
    items: list[Candle] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            items.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    opened_at=datetime.fromisoformat(_required(row, "opened_at")),
                    closed_at=datetime.fromisoformat(_required(row, "closed_at")),
                    open=Decimal(_required(row, "open")),
                    high=Decimal(_required(row, "high")),
                    low=Decimal(_required(row, "low")),
                    close=Decimal(_required(row, "close")),
                    volume=Decimal(_required(row, "volume")),
                    is_closed=True,
                )
            )
    return tuple(items)


def _download_dataset(
    config: EntryResearchV3Config,
    *,
    dataset_dir: Path,
) -> tuple[
    tuple[Candle, ...],
    tuple[Candle, ...],
    tuple[Candle, ...],
    tuple[FlowBucket, ...],
    datetime,
    datetime,
]:
    server_ms = _server_time_ms(config.endpoint)
    server_now = datetime.fromtimestamp(server_ms / 1000, UTC)
    latest_trade_day = config.latest_trade_day_override
    if latest_trade_day is None:
        latest_trade_day = discover_latest_archive_day(config, server_now=server_now)
    elif latest_trade_day >= server_now.date():
        raise ValueError(
            'latest trade day override must be a completed UTC day before server date'
        )
    evaluation_end = datetime.combine(latest_trade_day + timedelta(days=1), dt_time.min, UTC)
    evaluation_start = evaluation_end - timedelta(days=config.days)
    download_start = evaluation_start - timedelta(days=config.warmup_days)
    start_ms = int(download_start.timestamp() * 1000)
    end_ms = int(evaluation_end.timestamp() * 1000) - 1

    print(
        f"Frozen 90d evaluation: {evaluation_start.isoformat()} .. {evaluation_end.isoformat()} "
        f"(latest complete trade tape {latest_trade_day.isoformat()})"
    )
    if config.latest_trade_day_override is not None:
        archive_start_day = evaluation_start.date() - timedelta(days=1)
        missing: list[date] = []
        probe_day = archive_start_day
        while probe_day <= latest_trade_day:
            if not _archive_exists(
                _archive_url(config.archive_root, config.symbol, probe_day)
            ):
                missing.append(probe_day)
            probe_day += timedelta(days=1)
        if missing:
            rendered = ", ".join(item.isoformat() for item in missing)
            raise FileNotFoundError(
                'fixed-window public trade archives are incomplete for '
                f'{config.symbol}: {rendered}'
            )

    datasets: dict[str, tuple[Candle, ...]] = {}
    for interval in ("5", "15", "60"):
        print(f"Download kline {interval}m")
        datasets[interval] = tuple(
            item
            for item in download_klines(
                endpoint=config.endpoint,
                symbol=config.symbol,
                interval=interval,
                start_ms=start_ms,
                end_ms=end_ms,
            )
            if item.closed_at <= evaluation_end
        )
    five, fifteen, hourly = datasets["5"], datasets["15"], datasets["60"]
    dataset_dir.mkdir(parents=True, exist_ok=True)
    _write_candles(dataset_dir / "trade_5m.csv", five)
    _write_candles(dataset_dir / "trade_15m.csv", fifteen)
    _write_candles(dataset_dir / "trade_60m.csv", hourly)

    archive_dir = dataset_dir / "public_trades"
    archive_start_day = evaluation_start.date() - timedelta(days=1)
    archive_paths: list[Path] = []
    archive_hashes: dict[str, str] = {}
    day = archive_start_day
    while day <= latest_trade_day:
        filename = f"{config.symbol}{day.isoformat()}.csv.gz"
        destination = archive_dir / filename
        url = _archive_url(config.archive_root, config.symbol, day)
        print(f"Download public trades {day.isoformat()}")
        try:
            _download_with_resume(url, destination)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise FileNotFoundError(f"missing public trade archive: {url}") from exc
            raise
        archive_paths.append(destination)
        archive_hashes[filename] = _sha256_file(destination)
        day += timedelta(days=1)

    flow = aggregate_public_trade_archives(
        tuple(archive_paths),
        start_at=evaluation_start - timedelta(minutes=30),
        end_at=evaluation_end,
    )
    flow_path = dataset_dir / "flow_1m.csv"
    _write_flow(flow_path, flow)

    open_interest_path = dataset_dir / "open_interest_5m.csv"
    open_interest_rows = 0
    if config.include_open_interest:
        print("Download open interest 5m")
        open_interest = download_open_interest(
            config, start_at=evaluation_start, end_at=evaluation_end
        )
        _write_open_interest(open_interest_path, open_interest)
        open_interest_rows = len(open_interest)

    manifest = {
        "format": 1,
        "symbol": config.symbol,
        "endpoint": config.endpoint,
        "archive_root": config.archive_root,
        "latest_complete_trade_day": latest_trade_day,
        "download_start": download_start,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "days": config.days,
        "warmup_days": config.warmup_days,
        "five_minute_fingerprint": _fingerprint(five),
        "fifteen_minute_fingerprint": _fingerprint(fifteen),
        "hourly_fingerprint": _fingerprint(hourly),
        "flow_1m_sha256": _sha256_file(flow_path),
        "open_interest_5m_sha256": (
            _sha256_file(open_interest_path) if open_interest_path.exists() else None
        ),
        "five_minute_rows": len(five),
        "fifteen_minute_rows": len(fifteen),
        "hourly_rows": len(hourly),
        "flow_1m_rows": len(flow),
        "open_interest_5m_rows": open_interest_rows,
        "public_trade_archives": archive_hashes,
        "notes": [
            "Public trade side is interpreted as taker side Buy/Sell.",
            "Flow buckets contain only completed public trades before the evaluation end.",
            (
                "Raw daily .csv.gz tapes are retained so later flow logic can be "
                "recomputed without redownload."
            ),
        ],
    }
    _write_json(dataset_dir / "dataset_manifest.json", manifest)
    return five, fifteen, hourly, flow, evaluation_start, evaluation_end


def _load_dataset(
    dataset_dir: Path, *, config: EntryResearchV3Config
) -> tuple[
    tuple[Candle, ...],
    tuple[Candle, ...],
    tuple[Candle, ...],
    tuple[FlowBucket, ...],
    datetime,
    datetime,
]:
    manifest_path = dataset_dir / "dataset_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"dataset manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if str(manifest.get("symbol", "")).upper() != config.symbol.upper():
        raise ValueError("dataset symbol does not match requested symbol")
    if int(manifest.get("days", 0)) != config.days:
        raise ValueError("dataset days do not match requested days")
    evaluation_start = datetime.fromisoformat(str(manifest["evaluation_start"]))
    evaluation_end = datetime.fromisoformat(str(manifest["evaluation_end"]))
    five = _read_candles(dataset_dir / "trade_5m.csv", symbol=config.symbol, timeframe="5")
    fifteen = _read_candles(
        dataset_dir / "trade_15m.csv", symbol=config.symbol, timeframe="15"
    )
    hourly = _read_candles(dataset_dir / "trade_60m.csv", symbol=config.symbol, timeframe="60")
    flow = _read_flow(dataset_dir / "flow_1m.csv")
    checks = {
        "five_minute_fingerprint": _fingerprint(five),
        "fifteen_minute_fingerprint": _fingerprint(fifteen),
        "hourly_fingerprint": _fingerprint(hourly),
        "flow_1m_sha256": _sha256_file(dataset_dir / "flow_1m.csv"),
    }
    for key, actual in checks.items():
        if str(manifest.get(key)) != actual:
            raise ValueError(f"dataset fingerprint mismatch: {key}")
    if config.latest_trade_day_override is not None:
        manifest_day = date.fromisoformat(str(manifest["latest_complete_trade_day"]))
        if manifest_day != config.latest_trade_day_override:
            raise ValueError(
                "dataset fixed-window mismatch: "
                f"manifest={manifest_day.isoformat()} requested="
                f"{config.latest_trade_day_override.isoformat()}"
            )
    return five, fifteen, hourly, flow, evaluation_start, evaluation_end


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "P30 90-day entry research: 15m/5m local zones, soft 1h context, "
            "post-shock reset and frozen taker-flow tapes"
        )
    )
    parser.add_argument("--symbol", default="UNIUSDT")
    parser.add_argument("--endpoint", default="https://api.bybit.kz")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--five-lookback", type=int, default=130)
    parser.add_argument("--fifteen-lookback", type=int, default=130)
    parser.add_argument("--hourly-lookback", type=int, default=130)
    parser.add_argument("--atr-period", type=int, default=200)
    parser.add_argument("--zone-half-width-atr", default="0.5")
    parser.add_argument("--confluence-max-gap-percent", default="0.25")
    parser.add_argument("--cooldown-minutes", type=int, default=30)
    parser.add_argument("--shock-atr-multiple", default="3.0")
    parser.add_argument("--embargo-minutes-after-shock", type=int, default=60)
    parser.add_argument("--archive-root", default="https://public.bybit.com/trading")
    parser.add_argument(
        "--latest-trade-day",
        help=(
            "Freeze the dataset to this completed UTC trade day (YYYY-MM-DD). "
            "The evaluation end is the following 00:00 UTC."
        ),
    )
    parser.add_argument("--dataset-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--skip-open-interest", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = EntryResearchV3Config(
        symbol=args.symbol.strip().upper(),
        endpoint=args.endpoint.rstrip("/"),
        days=args.days,
        warmup_days=args.warmup_days,
        five_minute_lookback=args.five_lookback,
        fifteen_minute_lookback=args.fifteen_lookback,
        hourly_lookback=args.hourly_lookback,
        atr_period=args.atr_period,
        zone_half_width_atr=Decimal(str(args.zone_half_width_atr)),
        confluence_max_gap_percent=Decimal(str(args.confluence_max_gap_percent)),
        cooldown_minutes=args.cooldown_minutes,
        shock_atr_multiple=Decimal(str(args.shock_atr_multiple)),
        embargo_minutes_after_shock=args.embargo_minutes_after_shock,
        archive_root=args.archive_root.rstrip("/"),
        include_open_interest=not args.skip_open_interest,
        latest_trade_day_override=(
            date.fromisoformat(args.latest_trade_day) if args.latest_trade_day else None
        ),
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("reports") / "entry_research_v3" / f"{config.symbol}_{stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir = Path(args.dataset_dir) if args.dataset_dir else output_dir / "dataset"

    if args.dataset_dir and (dataset_dir / "dataset_manifest.json").exists():
        print(f"Loading frozen P30 dataset: {dataset_dir}")
        five, fifteen, hourly, flow, evaluation_start, evaluation_end = _load_dataset(
            dataset_dir, config=config
        )
    else:
        if args.dataset_dir:
            print(f"Resuming incomplete P30 dataset: {dataset_dir}")
        five, fifteen, hourly, flow, evaluation_start, evaluation_end = _download_dataset(
            config, dataset_dir=dataset_dir
        )

    local = run_local_mtf_research(
        five, fifteen, hourly, config, evaluation_start=evaluation_start
    )
    local = enrich_with_flow(local, flow)

    # Replay P29 on the same kline bytes for reference. P29's <= cooldown semantics mean
    # cooldown_bars=5 is the closest exact 30-minute spacing (next signal at i+6).
    legacy_config = EntryResearchV2Config(
        symbol=config.symbol,
        endpoint=config.endpoint,
        days=config.days,
        warmup_days=config.warmup_days,
        five_minute_lookback=config.five_minute_lookback,
        fifteen_minute_lookback=config.fifteen_minute_lookback,
        hourly_lookback=config.hourly_lookback,
        atr_period=config.atr_period,
        zone_half_width_atr=config.zone_half_width_atr,
        confluence_max_gap_percent=config.confluence_max_gap_percent,
        cooldown_bars=5,
        horizons_minutes=config.horizons_minutes,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        embargo_minutes_after_shock=config.embargo_minutes_after_shock,
    )
    legacy = run_mtf15_regime_research(
        five, fifteen, hourly, legacy_config, evaluation_start=evaluation_start
    )

    local_dir = output_dir / "p30_local_soft_hourly"
    local_dir.mkdir(parents=True, exist_ok=True)
    _write_v3_signals(local_dir / "signals.csv", local)
    slices = _slice_summary(local.signals, config=config, evaluation_start=evaluation_start)
    stability = _stability_summary(slices)
    _write_json(
        local_dir / "summary.json",
        {
            "architecture": "p30_local_soft_hourly",
            "evaluation_start": evaluation_start,
            "evaluation_end": evaluation_end,
            "config": asdict(config),
            "summary": local.summary,
            "thirty_day_slices": slices,
            "stability": stability,
        },
    )

    legacy_dir = output_dir / "p29_hard_hourly_same_dataset"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    from bybit_workbench.research.mtf_entry_v2 import _write_v2_signals

    _write_v2_signals(legacy_dir / "signals.csv", legacy)
    _write_json(
        legacy_dir / "summary.json",
        {
            "architecture": "p29_hard_hourly_same_dataset",
            "config": asdict(legacy_config),
            "summary": legacy.summary,
        },
    )

    comparison = {
        "dataset_dir": str(dataset_dir),
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "fingerprints": {
            "5m": local.five_minute_fingerprint,
            "15m": local.fifteen_minute_fingerprint,
            "60m": local.hourly_fingerprint,
            "flow_1m": _sha256_file(dataset_dir / "flow_1m.csv"),
        },
        "p29_hard_hourly": legacy.summary,
        "p30_local_soft_hourly": local.summary,
        "thirty_day_slices": slices,
        "stability": stability,
        "notes": [
            "P30 does not forbid a short during a long 1h context or vice versa.",
            "The 1h context is recorded only for later scoring/analysis.",
            "15m+5m confluence defines the candidate entry location.",
            (
                "After a shock, the shock candle itself and all older candles are "
                "excluded from the new zone."
            ),
            (
                "Cooldown is exact elapsed 5m bars: 30 minutes means the next candidate "
                "may appear 30 minutes later."
            ),
            "Taker-flow features are descriptive only in P30; they do not filter candidates.",
        ],
    }
    _write_json(output_dir / "comparison.json", comparison)

    print(f"Dataset: {dataset_dir}")
    print(
        "P29 hard-hourly reference: "
        f"signals={legacy.summary.get('signals', 0)} "
        f"signals/day={legacy.summary.get('signals_per_day', 0)}"
    )
    print(
        "P30 local + soft 1h: "
        f"signals={local.summary.get('signals', 0)} "
        f"signals/day={local.summary.get('signals_per_day', 0)}"
    )
    print(f"Report: {output_dir / 'comparison.json'}")
    print("Raw public trade archives are retained under dataset\\public_trades for future layers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
