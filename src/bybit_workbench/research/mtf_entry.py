from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.domain.models import Candle
from bybit_workbench.strategies.indicators import latest_wilder_atr, true_ranges

Variant = Literal["fixed", "adaptive"]
Direction = Literal["Long", "Short"]


@dataclass(frozen=True, slots=True)
class ZoneSnapshot:
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
class EntryResearchConfig:
    symbol: str = "UNIUSDT"
    endpoint: str = "https://api.bybit.kz"
    days: int = 30
    warmup_days: int = 14
    five_minute_lookback: int = 130
    hourly_lookback: int = 130
    atr_period: int = 200
    zone_half_width_atr: Decimal = Decimal("0.5")
    confluence_max_gap_percent: Decimal = Decimal("0.25")
    cooldown_bars: int = 12
    horizons_minutes: tuple[int, ...] = (30, 60, 120, 240)
    variant: Variant = "fixed"
    shock_atr_period: int = 20
    shock_atr_multiple: Decimal = Decimal("3.0")
    minimum_five_minute_regime_bars: int = 24
    minimum_hourly_regime_bars: int = 8

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.days <= 0 or self.warmup_days < 0:
            raise ValueError("days must be positive and warmup_days cannot be negative")
        if self.five_minute_lookback <= 0 or self.hourly_lookback <= 0:
            raise ValueError("lookbacks must be positive")
        if self.atr_period <= 1:
            raise ValueError("atr_period must be greater than one")
        if self.zone_half_width_atr <= 0:
            raise ValueError("zone_half_width_atr must be positive")
        if self.confluence_max_gap_percent < 0:
            raise ValueError("confluence_max_gap_percent cannot be negative")
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars cannot be negative")
        invalid_horizons = any(
            value <= 0 or value % 5 for value in self.horizons_minutes
        )
        if not self.horizons_minutes or invalid_horizons:
            raise ValueError("horizons must be positive multiples of five minutes")
        if self.variant not in {"fixed", "adaptive"}:
            raise ValueError("variant must be fixed or adaptive")


@dataclass(frozen=True, slots=True)
class EntrySignal:
    variant: Variant
    symbol: str
    direction: Direction
    entry_at: datetime
    entry_price: Decimal
    hourly_zone_low: Decimal
    hourly_zone_high: Decimal
    five_zone_low: Decimal
    five_zone_high: Decimal
    zone_gap_percent: Decimal
    confluence_score: Decimal
    hourly_effective_lookback: int
    five_effective_lookback: int
    hourly_regime_reset_at: datetime | None
    five_regime_reset_at: datetime | None
    metrics: dict[str, Decimal | int | str | None]


@dataclass(frozen=True, slots=True)
class EntryResearchResult:
    config: EntryResearchConfig
    signals: tuple[EntrySignal, ...]
    summary: dict[str, Any]
    five_minute_fingerprint: str
    hourly_fingerprint: str


def _interval_milliseconds(interval: str) -> int:
    mapping = {"5": 5 * 60_000, "15": 15 * 60_000, "60": 60 * 60_000}
    try:
        return mapping[interval]
    except KeyError as exc:
        raise ValueError(f"unsupported research interval: {interval}") from exc


def _request_json(url: str, *, timeout: float = 20.0, retries: int = 4) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "BybitStrategyWorkbench/0.8.5"}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Bybit response is not a JSON object")
            if int(payload.get("retCode", -1)) != 0:
                raise RuntimeError(
                    f"Bybit retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}"
                )
            return payload
        except Exception as exc:  # pragma: no cover - network retry path
            last_error = exc
            if attempt + 1 >= retries:
                break
            time.sleep(0.4 * (2**attempt))
    assert last_error is not None
    raise RuntimeError(f"Bybit request failed: {last_error}") from last_error


def _server_time_ms(endpoint: str) -> int:
    payload = _request_json(f"{endpoint.rstrip('/')}/v5/market/time")
    result = payload.get("result", {})
    if isinstance(result, dict):
        nano = result.get("timeNano")
        if nano not in (None, ""):
            return int(str(nano)) // 1_000_000
        seconds = result.get("timeSecond")
        if seconds not in (None, ""):
            return int(str(seconds)) * 1000
    return int(payload.get("time", 0))


def download_klines(
    *,
    endpoint: str,
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
) -> tuple[Candle, ...]:
    interval_ms = _interval_milliseconds(interval)
    selected: dict[int, Candle] = {}
    cursor_end = end_ms
    while cursor_end >= start_ms:
        query = urllib.parse.urlencode(
            {
                "category": "linear",
                "symbol": symbol.upper(),
                "interval": interval,
                "start": start_ms,
                "end": cursor_end,
                "limit": 1000,
            }
        )
        payload = _request_json(f"{endpoint.rstrip('/')}/v5/market/kline?{query}")
        result = payload.get("result", {})
        rows = result.get("list", []) if isinstance(result, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        timestamps: list[int] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 7:
                continue
            opened_ms = int(str(row[0]))
            timestamps.append(opened_ms)
            if opened_ms < start_ms or opened_ms > end_ms:
                continue
            opened_at = datetime.fromtimestamp(opened_ms / 1000, UTC)
            closed_at = datetime.fromtimestamp((opened_ms + interval_ms) / 1000, UTC)
            selected[opened_ms] = Candle(
                symbol=symbol.upper(),
                timeframe=interval,
                opened_at=opened_at,
                closed_at=closed_at,
                open=Decimal(str(row[1])),
                high=Decimal(str(row[2])),
                low=Decimal(str(row[3])),
                close=Decimal(str(row[4])),
                volume=Decimal(str(row[5])),
                is_closed=True,
            )
        if not timestamps:
            break
        earliest = min(timestamps)
        if earliest <= start_ms:
            break
        next_end = earliest - 1
        if next_end >= cursor_end:
            raise RuntimeError("Bybit kline pagination did not advance")
        cursor_end = next_end
    return tuple(selected[key] for key in sorted(selected))


def _fingerprint(candles: Sequence[Candle]) -> str:
    digest = hashlib.sha256()
    for candle in candles:
        digest.update(
            (
                f"{candle.symbol}|{candle.timeframe}|{candle.opened_at.isoformat()}|"
                f"{candle.open}|{candle.high}|{candle.low}|{candle.close}|{candle.volume}\n"
            ).encode()
        )
    return digest.hexdigest()


def _zone_gap_percent(
    first_low: Decimal,
    first_high: Decimal,
    second_low: Decimal,
    second_high: Decimal,
    reference: Decimal,
) -> Decimal:
    if max(first_low, second_low) <= min(first_high, second_high):
        return Decimal("0")
    gap = (
        second_low - first_high
        if first_high < second_low
        else first_low - second_high
    )
    return abs(gap) / reference * Decimal("100")


def _confluence_score(gap_percent: Decimal, maximum_gap_percent: Decimal) -> Decimal:
    if gap_percent <= 0:
        return Decimal("100")
    if maximum_gap_percent <= 0 or gap_percent >= maximum_gap_percent:
        return Decimal("0")
    return (Decimal("1") - gap_percent / maximum_gap_percent) * Decimal("100")


def _find_regime_reset(
    candles: Sequence[Candle],
    *,
    max_lookback: int,
    atr_period: int,
    multiple: Decimal,
) -> int | None:
    if len(candles) < atr_period + 2:
        return None
    start = max(atr_period, len(candles) - max_lookback)
    ranges = true_ranges(candles)
    latest: int | None = None
    for index in range(start, len(candles)):
        prior_ranges = ranges[index - atr_period : index]
        baseline = sum(prior_ranges, Decimal("0")) / Decimal(atr_period)
        if baseline > 0 and ranges[index] >= multiple * baseline:
            latest = index
    return latest


def _build_zone(
    history: Sequence[Candle],
    *,
    observed_at: datetime,
    timeframe: str,
    lookback: int,
    atr_period: int,
    width_atr: Decimal,
    variant: Variant,
    shock_atr_period: int,
    shock_atr_multiple: Decimal,
    minimum_regime_bars: int,
) -> ZoneSnapshot | None:
    if len(history) < max(lookback, atr_period):
        return None
    atr = latest_wilder_atr(history, atr_period)
    if atr is None or atr <= 0:
        return None
    selected = history[-lookback:]
    reset_at: datetime | None = None
    if variant == "adaptive":
        reset_index = _find_regime_reset(
            history,
            max_lookback=lookback,
            atr_period=shock_atr_period,
            multiple=shock_atr_multiple,
        )
        if reset_index is not None:
            candidate = history[reset_index:]
            if len(candidate) < minimum_regime_bars:
                return None
            selected = candidate[-lookback:]
            reset_at = history[reset_index].opened_at
    range_high = max(item.high for item in selected)
    range_low = min(item.low for item in selected)
    width = width_atr * atr
    support_top = range_low + width
    support_bottom = range_low - width
    resistance_top = range_high + width
    resistance_bottom = range_high - width
    if support_top >= resistance_bottom:
        return None
    return ZoneSnapshot(
        timeframe=timeframe,
        observed_at=observed_at,
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


def _directional_excursions(
    direction: Direction,
    entry: Decimal,
    future: Sequence[Candle],
) -> tuple[Decimal, Decimal]:
    if not future:
        return Decimal("0"), Decimal("0")
    if direction == "Long":
        best = max(item.high for item in future)
        worst = min(item.low for item in future)
        mfe = (best / entry - Decimal("1")) * Decimal("100")
        mae = (worst / entry - Decimal("1")) * Decimal("100")
    else:
        best = min(item.low for item in future)
        worst = max(item.high for item in future)
        mfe = (Decimal("1") - best / entry) * Decimal("100")
        mae = (Decimal("1") - worst / entry) * Decimal("100")
    return mfe, mae


def _first_hit(
    direction: Direction,
    entry: Decimal,
    future: Sequence[Candle],
    favorable_percent: Decimal,
    adverse_percent: Decimal,
) -> str:
    favorable = favorable_percent / Decimal("100")
    adverse = adverse_percent / Decimal("100")
    for candle in future:
        if direction == "Long":
            favorable_hit = candle.high >= entry * (Decimal("1") + favorable)
            adverse_hit = candle.low <= entry * (Decimal("1") - adverse)
        else:
            favorable_hit = candle.low <= entry * (Decimal("1") - favorable)
            adverse_hit = candle.high >= entry * (Decimal("1") + adverse)
        if favorable_hit and adverse_hit:
            return "ambiguous_same_bar"
        if favorable_hit:
            return "favorable_first"
        if adverse_hit:
            return "adverse_first"
    return "neither"


def _metrics_for_signal(
    direction: Direction,
    entry: Decimal,
    candles: Sequence[Candle],
    entry_index: int,
    horizons: Sequence[int],
) -> dict[str, Decimal | int | str | None]:
    metrics: dict[str, Decimal | int | str | None] = {}
    for horizon in horizons:
        bars = horizon // 5
        future = candles[entry_index + 1 : entry_index + 1 + bars]
        mfe, mae = _directional_excursions(direction, entry, future)
        metrics[f"mfe_{horizon}m_pct"] = mfe
        metrics[f"mae_{horizon}m_pct"] = mae
    maximum_bars = max(horizons) // 5
    future = candles[entry_index + 1 : entry_index + 1 + maximum_bars]
    for target in (Decimal("0.5"), Decimal("1"), Decimal("2"), Decimal("3"), Decimal("5")):
        label = str(target).replace(".", "_")
        metrics[f"hit_plus_{label}_pct"] = int(
            _directional_excursions(direction, entry, future)[0] >= target
        )
    metrics["first_0_5_vs_0_5"] = _first_hit(
        direction, entry, future, Decimal("0.5"), Decimal("0.5")
    )
    metrics["first_0_5_vs_1_0"] = _first_hit(
        direction, entry, future, Decimal("0.5"), Decimal("1.0")
    )
    return metrics


def _percent(value: int, total: int) -> float:
    return 0.0 if total == 0 else round(value * 100.0 / total, 2)


def _summary(signals: Sequence[EntrySignal], horizons: Sequence[int]) -> dict[str, Any]:
    total = len(signals)
    result: dict[str, Any] = {
        "signals": total,
        "long": sum(item.direction == "Long" for item in signals),
        "short": sum(item.direction == "Short" for item in signals),
    }
    if not signals:
        return result
    result["average_confluence_score"] = round(
        float(sum((item.confluence_score for item in signals), Decimal("0")) / Decimal(total)),
        3,
    )
    result["median_zone_gap_percent"] = round(
        float(statistics.median(item.zone_gap_percent for item in signals)), 5
    )
    for horizon in horizons:
        mfe_values = [Decimal(str(item.metrics[f"mfe_{horizon}m_pct"])) for item in signals]
        mae_values = [Decimal(str(item.metrics[f"mae_{horizon}m_pct"])) for item in signals]
        result[f"median_mfe_{horizon}m_pct"] = round(float(statistics.median(mfe_values)), 4)
        result[f"median_mae_{horizon}m_pct"] = round(float(statistics.median(mae_values)), 4)
    for target in ("0_5", "1", "2", "3", "5"):
        hits = 0
        metric_key = f"hit_plus_{target}_pct"
        for item in signals:
            metric_value = item.metrics[metric_key]
            if not isinstance(metric_value, int):
                raise TypeError(f"{metric_key} must be an int")
            hits += metric_value
        result[f"hit_plus_{target}_pct_rate"] = _percent(hits, total)
    for key in ("first_0_5_vs_0_5", "first_0_5_vs_1_0"):
        outcome_names = (
            "favorable_first",
            "adverse_first",
            "ambiguous_same_bar",
            "neither",
        )
        counts = {name: 0 for name in outcome_names}
        for item in signals:
            counts[str(item.metrics[key])] += 1
        result[key] = {
            name: {"count": count, "percent": _percent(count, total)}
            for name, count in counts.items()
        }
    return result


def run_entry_research(
    five_minute: Sequence[Candle],
    hourly: Sequence[Candle],
    config: EntryResearchConfig,
    *,
    evaluation_start: datetime | None = None,
) -> EntryResearchResult:
    if any(item.symbol != config.symbol.upper() or item.timeframe != "5" for item in five_minute):
        raise ValueError("five-minute dataset does not match symbol/timeframe")
    if any(item.symbol != config.symbol.upper() or item.timeframe != "60" for item in hourly):
        raise ValueError("hourly dataset does not match symbol/timeframe")
    five = tuple(sorted(five_minute, key=lambda item: item.opened_at))
    hours = tuple(sorted(hourly, key=lambda item: item.opened_at))
    if not five or not hours:
        raise ValueError("both five-minute and hourly datasets are required")

    signals: list[EntrySignal] = []
    hourly_end = 0
    cooldown_until_index = -1
    minimum_five = max(config.five_minute_lookback, config.atr_period)
    minimum_hourly = max(config.hourly_lookback, config.atr_period)

    for index, bar in enumerate(five):
        if index < minimum_five:
            continue
        if evaluation_start is not None and bar.opened_at < evaluation_start:
            continue
        if index <= cooldown_until_index:
            continue
        while hourly_end < len(hours) and hours[hourly_end].closed_at <= bar.opened_at:
            hourly_end += 1
        if hourly_end < minimum_hourly:
            continue
        five_zone = _build_zone(
            five[:index],
            observed_at=bar.opened_at,
            timeframe="5",
            lookback=config.five_minute_lookback,
            atr_period=config.atr_period,
            width_atr=config.zone_half_width_atr,
            variant=config.variant,
            shock_atr_period=config.shock_atr_period,
            shock_atr_multiple=config.shock_atr_multiple,
            minimum_regime_bars=config.minimum_five_minute_regime_bars,
        )
        hourly_zone = _build_zone(
            hours[:hourly_end],
            observed_at=bar.opened_at,
            timeframe="60",
            lookback=config.hourly_lookback,
            atr_period=config.atr_period,
            width_atr=config.zone_half_width_atr,
            variant=config.variant,
            shock_atr_period=config.shock_atr_period,
            shock_atr_multiple=config.shock_atr_multiple,
            minimum_regime_bars=config.minimum_hourly_regime_bars,
        )
        if five_zone is None or hourly_zone is None:
            continue
        reference = bar.open
        long_gap = _zone_gap_percent(
            hourly_zone.support_bottom,
            hourly_zone.support_top,
            five_zone.support_bottom,
            five_zone.support_top,
            reference,
        )
        short_gap = _zone_gap_percent(
            hourly_zone.resistance_bottom,
            hourly_zone.resistance_top,
            five_zone.resistance_bottom,
            five_zone.resistance_top,
            reference,
        )
        long_entry = five_zone.support_top
        short_entry = five_zone.resistance_bottom
        long_ok = (
            long_gap <= config.confluence_max_gap_percent and bar.low <= long_entry
        )
        short_ok = (
            short_gap <= config.confluence_max_gap_percent and bar.high >= short_entry
        )
        # If one five-minute bar crosses both qualified MTF zones, OHLC does not
        # reveal which side was reached first. Discard it instead of inventing a path.
        if long_ok == short_ok:
            continue
        if long_ok:
            direction: Direction = "Long"
            entry = long_entry
            gap = long_gap
            hourly_low, hourly_high = hourly_zone.support_bottom, hourly_zone.support_top
            five_low, five_high = five_zone.support_bottom, five_zone.support_top
        else:
            direction = "Short"
            entry = short_entry
            gap = short_gap
            hourly_low, hourly_high = hourly_zone.resistance_bottom, hourly_zone.resistance_top
            five_low, five_high = five_zone.resistance_bottom, five_zone.resistance_top
        metrics = _metrics_for_signal(direction, entry, five, index, config.horizons_minutes)
        signals.append(
            EntrySignal(
                variant=config.variant,
                symbol=config.symbol.upper(),
                direction=direction,
                entry_at=bar.opened_at,
                entry_price=entry,
                hourly_zone_low=hourly_low,
                hourly_zone_high=hourly_high,
                five_zone_low=five_low,
                five_zone_high=five_high,
                zone_gap_percent=gap,
                confluence_score=_confluence_score(gap, config.confluence_max_gap_percent),
                hourly_effective_lookback=hourly_zone.effective_lookback,
                five_effective_lookback=five_zone.effective_lookback,
                hourly_regime_reset_at=hourly_zone.regime_reset_at,
                five_regime_reset_at=five_zone.regime_reset_at,
                metrics=metrics,
            )
        )
        cooldown_until_index = index + config.cooldown_bars

    return EntryResearchResult(
        config=config,
        signals=tuple(signals),
        summary=_summary(signals, config.horizons_minutes),
        five_minute_fingerprint=_fingerprint(five),
        hourly_fingerprint=_fingerprint(hours),
    )


def _decimal_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _write_candles(path: Path, candles: Iterable[Candle]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("opened_at", "closed_at", "open", "high", "low", "close", "volume"))
        for item in candles:
            writer.writerow(
                (
                    item.opened_at.isoformat(),
                    item.closed_at.isoformat(),
                    item.open,
                    item.high,
                    item.low,
                    item.close,
                    item.volume,
                )
            )


def _write_signals(path: Path, result: EntryResearchResult) -> None:
    horizons = result.config.horizons_minutes
    metric_names = [
        *(f"mfe_{value}m_pct" for value in horizons),
        *(f"mae_{value}m_pct" for value in horizons),
        "hit_plus_0_5_pct",
        "hit_plus_1_pct",
        "hit_plus_2_pct",
        "hit_plus_3_pct",
        "hit_plus_5_pct",
        "first_0_5_vs_0_5",
        "first_0_5_vs_1_0",
    ]
    fields = [
        "variant",
        "symbol",
        "direction",
        "entry_at",
        "entry_price",
        "hourly_zone_low",
        "hourly_zone_high",
        "five_zone_low",
        "five_zone_high",
        "zone_gap_percent",
        "confluence_score",
        "hourly_effective_lookback",
        "five_effective_lookback",
        "hourly_regime_reset_at",
        "five_regime_reset_at",
        *metric_names,
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for signal in result.signals:
            row = {
                "variant": signal.variant,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "entry_at": signal.entry_at.isoformat(),
                "entry_price": signal.entry_price,
                "hourly_zone_low": signal.hourly_zone_low,
                "hourly_zone_high": signal.hourly_zone_high,
                "five_zone_low": signal.five_zone_low,
                "five_zone_high": signal.five_zone_high,
                "zone_gap_percent": signal.zone_gap_percent,
                "confluence_score": signal.confluence_score,
                "hourly_effective_lookback": signal.hourly_effective_lookback,
                "five_effective_lookback": signal.five_effective_lookback,
                "hourly_regime_reset_at": (
                    ""
                    if signal.hourly_regime_reset_at is None
                    else signal.hourly_regime_reset_at.isoformat()
                ),
                "five_regime_reset_at": (
                    ""
                    if signal.five_regime_reset_at is None
                    else signal.five_regime_reset_at.isoformat()
                ),
            }
            row.update(signal.metrics)
            writer.writerow(row)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Research MTF 1H+5m zone-confluence entry quality without changing "
            "live strategy exits."
        )
    )
    parser.add_argument("--symbol", default="UNIUSDT")
    parser.add_argument("--endpoint", default="https://api.bybit.kz")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--variant", choices=("fixed", "adaptive"), default="fixed")
    parser.add_argument("--five-lookback", type=int, default=130)
    parser.add_argument("--hourly-lookback", type=int, default=130)
    parser.add_argument("--atr-period", type=int, default=200)
    parser.add_argument("--zone-half-width-atr", default="0.5")
    parser.add_argument("--confluence-max-gap-percent", default="0.25")
    parser.add_argument("--cooldown-bars", type=int, default=12)
    parser.add_argument("--shock-atr-multiple", default="3.0")
    parser.add_argument("--output-dir")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    config = EntryResearchConfig(
        symbol=args.symbol.strip().upper(),
        endpoint=args.endpoint.rstrip("/"),
        days=args.days,
        warmup_days=args.warmup_days,
        five_minute_lookback=args.five_lookback,
        hourly_lookback=args.hourly_lookback,
        atr_period=args.atr_period,
        zone_half_width_atr=Decimal(str(args.zone_half_width_atr)),
        confluence_max_gap_percent=Decimal(str(args.confluence_max_gap_percent)),
        cooldown_bars=args.cooldown_bars,
        variant=args.variant,
        shock_atr_multiple=Decimal(str(args.shock_atr_multiple)),
    )
    server_ms = _server_time_ms(config.endpoint)
    server_now = datetime.fromtimestamp(server_ms / 1000, UTC)
    evaluation_start = server_now - timedelta(days=config.days)
    download_start = evaluation_start - timedelta(days=config.warmup_days)
    start_ms = int(download_start.timestamp() * 1000)
    end_ms = server_ms
    print(
        f"Downloading {config.symbol}: {download_start.isoformat()} .. {server_now.isoformat()} "
        f"from {config.endpoint}"
    )
    five = tuple(
        item
        for item in download_klines(
            endpoint=config.endpoint,
            symbol=config.symbol,
            interval="5",
            start_ms=start_ms,
            end_ms=end_ms,
        )
        if int(item.closed_at.timestamp() * 1000) <= server_ms
    )
    hourly = tuple(
        item
        for item in download_klines(
            endpoint=config.endpoint,
            symbol=config.symbol,
            interval="60",
            start_ms=start_ms,
            end_ms=end_ms,
        )
        if int(item.closed_at.timestamp() * 1000) <= server_ms
    )
    result = run_entry_research(five, hourly, config, evaluation_start=evaluation_start)
    stamp = server_now.strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("reports") / "entry_research" / f"{config.symbol}_{stamp}_{config.variant}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_candles(output_dir / "trade_5m.csv", five)
    _write_candles(output_dir / "trade_60m.csv", hourly)
    _write_signals(output_dir / "signals.csv", result)
    manifest = {
        "generated_at": server_now.isoformat(),
        "evaluation_start": evaluation_start.isoformat(),
        "config": asdict(config),
        "five_minute_fingerprint": result.five_minute_fingerprint,
        "hourly_fingerprint": result.hourly_fingerprint,
        "summary": result.summary,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=_decimal_json),
        encoding="utf-8",
    )
    print(json.dumps(result.summary, ensure_ascii=False, indent=2, default=_decimal_json))
    print(f"Report: {output_dir.resolve()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
