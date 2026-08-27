from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.domain.models import Candle
from bybit_workbench.research.mtf_entry import (
    Direction,
    EntryResearchConfig,
    ZoneSnapshot,
    _build_zone,
    _decimal_json,
    _fingerprint,
    _metrics_for_signal,
    _server_time_ms,
    _write_candles,
    download_klines,
    run_entry_research,
)
from bybit_workbench.strategies.indicators import true_ranges, wilder_atr

Architecture = Literal["p28_adaptive", "mtf15_regime"]


@dataclass(frozen=True, slots=True)
class EntryResearchV2Config:
    symbol: str = "UNIUSDT"
    endpoint: str = "https://api.bybit.kz"
    days: int = 30
    warmup_days: int = 14
    five_minute_lookback: int = 130
    fifteen_minute_lookback: int = 130
    hourly_lookback: int = 130
    atr_period: int = 200
    zone_half_width_atr: Decimal = Decimal("0.5")
    confluence_max_gap_percent: Decimal = Decimal("0.25")
    cooldown_bars: int = 12
    horizons_minutes: tuple[int, ...] = (30, 60, 120, 240, 360)
    shock_atr_period: int = 20
    shock_atr_multiple: Decimal = Decimal("3.0")
    embargo_minutes_after_shock: int = 60

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.days <= 0 or self.warmup_days < 0:
            raise ValueError("days must be positive and warmup_days cannot be negative")
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
        if self.cooldown_bars < 0:
            raise ValueError("cooldown_bars cannot be negative")
        if self.embargo_minutes_after_shock < 0:
            raise ValueError("embargo_minutes_after_shock cannot be negative")
        if not self.horizons_minutes or any(
            value <= 0 or value % 5 for value in self.horizons_minutes
        ):
            raise ValueError("horizons must be positive multiples of five minutes")


@dataclass(frozen=True, slots=True)
class HourlyBias:
    direction: Direction
    return_percent: Decimal
    effective_lookback: int
    regime_reset_at: datetime | None


@dataclass(frozen=True, slots=True)
class EntrySignalV2:
    architecture: Architecture
    symbol: str
    direction: Direction
    entry_at: datetime
    entry_price: Decimal
    hourly_bias: Direction
    hourly_return_percent: Decimal
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
    metrics: dict[str, Decimal | int | str | None]


@dataclass(frozen=True, slots=True)
class EntryResearchV2Result:
    config: EntryResearchV2Config
    signals: tuple[EntrySignalV2, ...]
    summary: dict[str, Any]
    five_minute_fingerprint: str
    fifteen_minute_fingerprint: str
    hourly_fingerprint: str


def _zone_gap_percent(
    first_low: Decimal,
    first_high: Decimal,
    second_low: Decimal,
    second_high: Decimal,
    reference: Decimal,
) -> Decimal:
    if max(first_low, second_low) <= min(first_high, second_high):
        return Decimal("0")
    gap = second_low - first_high if first_high < second_low else first_low - second_high
    return abs(gap) / reference * Decimal("100")




def _precompute_adaptive_zones(
    candles: tuple[Candle, ...],
    *,
    timeframe: str,
    lookback: int,
    atr_period: int,
    width_atr: Decimal,
    shock_atr_period: int,
    shock_atr_multiple: Decimal,
    minimum_regime_bars: int,
) -> tuple[ZoneSnapshot | None, ...]:
    """Return zone for each causal history length i == candles[:i]."""

    count = len(candles)
    zones: list[ZoneSnapshot | None] = [None] * (count + 1)
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
            if history_len - reset_index < minimum_regime_bars:
                continue
            selected_start = reset_index
            reset_at = candles[reset_index].opened_at
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
        zones[history_len] = ZoneSnapshot(
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


def _hourly_bias(
    history: tuple[Candle, ...],
    *,
    observed_at: datetime,
    config: EntryResearchV2Config,
) -> HourlyBias | None:
    # The hourly chart is context only. We reuse the causal shock/reset machinery
    # to decide which part of history still belongs to the current market regime,
    # but we do not require the 1h zone to overlap the execution zones.
    zone = _build_zone(
        history,
        observed_at=observed_at,
        timeframe="60",
        lookback=config.hourly_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        variant="adaptive",
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=max(1, config.embargo_minutes_after_shock // 60),
    )
    if zone is None or zone.effective_lookback < 1:
        return None
    selected = history[-zone.effective_lookback :]
    if not selected:
        return None
    first = selected[0].close
    last = selected[-1].close
    if first <= 0 or last == first:
        return None
    return_percent = (last / first - Decimal("1")) * Decimal("100")
    direction: Direction = "Long" if return_percent > 0 else "Short"
    return HourlyBias(
        direction=direction,
        return_percent=return_percent,
        effective_lookback=zone.effective_lookback,
        regime_reset_at=zone.regime_reset_at,
    )


def _percent(value: int, total: int) -> float:
    return 0.0 if total == 0 else round(value * 100.0 / total, 2)


def _summary(
    signals: tuple[EntrySignalV2, ...],
    *,
    days: int,
    horizons: tuple[int, ...],
) -> dict[str, Any]:
    total = len(signals)
    result: dict[str, Any] = {
        "signals": total,
        "signals_per_day": round(total / days, 3) if days else 0.0,
        "long": sum(item.direction == "Long" for item in signals),
        "short": sum(item.direction == "Short" for item in signals),
    }
    if not signals:
        return result
    result["median_zone_gap_percent"] = round(
        float(statistics.median(item.zone_gap_percent for item in signals)), 5
    )
    result["median_hourly_return_percent"] = round(
        float(statistics.median(item.hourly_return_percent for item in signals)), 4
    )
    for horizon in horizons:
        mfe_values = [Decimal(str(item.metrics[f"mfe_{horizon}m_pct"])) for item in signals]
        mae_values = [Decimal(str(item.metrics[f"mae_{horizon}m_pct"])) for item in signals]
        result[f"median_mfe_{horizon}m_pct"] = round(float(statistics.median(mfe_values)), 4)
        result[f"median_mae_{horizon}m_pct"] = round(float(statistics.median(mae_values)), 4)
    for target in ("0_5", "1", "2", "3", "5"):
        key = f"hit_plus_{target}_pct"
        hits = 0
        for item in signals:
            value = item.metrics[key]
            if not isinstance(value, int):
                raise TypeError(f"{key} must be an int")
            hits += value
        result[f"hit_plus_{target}_pct_rate"] = _percent(hits, total)
    for key in ("first_0_5_vs_0_5", "first_0_5_vs_1_0"):
        names = ("favorable_first", "adverse_first", "ambiguous_same_bar", "neither")
        counts = {name: 0 for name in names}
        for item in signals:
            counts[str(item.metrics[key])] += 1
        result[key] = {
            name: {"count": count, "percent": _percent(count, total)}
            for name, count in counts.items()
        }
    return result


def run_mtf15_regime_research(
    five_minute: tuple[Candle, ...],
    fifteen_minute: tuple[Candle, ...],
    hourly: tuple[Candle, ...],
    config: EntryResearchV2Config,
    *,
    evaluation_start: datetime | None = None,
) -> EntryResearchV2Result:
    symbol = config.symbol.upper()
    if any(item.symbol != symbol or item.timeframe != "5" for item in five_minute):
        raise ValueError("five-minute dataset does not match symbol/timeframe")
    if any(item.symbol != symbol or item.timeframe != "15" for item in fifteen_minute):
        raise ValueError("fifteen-minute dataset does not match symbol/timeframe")
    if any(item.symbol != symbol or item.timeframe != "60" for item in hourly):
        raise ValueError("hourly dataset does not match symbol/timeframe")

    five = tuple(sorted(five_minute, key=lambda item: item.opened_at))
    fifteen = tuple(sorted(fifteen_minute, key=lambda item: item.opened_at))
    hours = tuple(sorted(hourly, key=lambda item: item.opened_at))
    if not five or not fifteen or not hours:
        raise ValueError("5m, 15m and 60m datasets are required")

    signals: list[EntrySignalV2] = []
    fifteen_end = 0
    hourly_end = 0
    cooldown_until_index = -1
    minimum_five = max(config.five_minute_lookback, config.atr_period)
    minimum_fifteen = max(config.fifteen_minute_lookback, config.atr_period)
    minimum_hourly = max(config.hourly_lookback, config.atr_period)
    five_embargo_bars = max(1, config.embargo_minutes_after_shock // 5)
    fifteen_embargo_bars = max(1, config.embargo_minutes_after_shock // 15)
    hourly_embargo_bars = max(1, config.embargo_minutes_after_shock // 60)

    five_zones = _precompute_adaptive_zones(
        five,
        timeframe="5",
        lookback=config.five_minute_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=five_embargo_bars,
    )
    fifteen_zones = _precompute_adaptive_zones(
        fifteen,
        timeframe="15",
        lookback=config.fifteen_minute_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=fifteen_embargo_bars,
    )
    hourly_zones = _precompute_adaptive_zones(
        hours,
        timeframe="60",
        lookback=config.hourly_lookback,
        atr_period=config.atr_period,
        width_atr=config.zone_half_width_atr,
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_regime_bars=hourly_embargo_bars,
    )

    for index, bar in enumerate(five):
        if index < minimum_five:
            continue
        if evaluation_start is not None and bar.opened_at < evaluation_start:
            continue
        if index <= cooldown_until_index:
            continue
        while fifteen_end < len(fifteen) and fifteen[fifteen_end].closed_at <= bar.opened_at:
            fifteen_end += 1
        while hourly_end < len(hours) and hours[hourly_end].closed_at <= bar.opened_at:
            hourly_end += 1
        if fifteen_end < minimum_fifteen or hourly_end < minimum_hourly:
            continue

        five_zone = five_zones[index]
        fifteen_zone = fifteen_zones[fifteen_end]
        hourly_zone = hourly_zones[hourly_end]
        if five_zone is None or fifteen_zone is None or hourly_zone is None:
            continue
        selected_hours = hours[hourly_end - hourly_zone.effective_lookback : hourly_end]
        if not selected_hours:
            continue
        first_hourly_close = selected_hours[0].close
        last_hourly_close = selected_hours[-1].close
        if first_hourly_close <= 0 or last_hourly_close == first_hourly_close:
            continue
        hourly_return = (last_hourly_close / first_hourly_close - Decimal("1")) * Decimal("100")
        hourly_direction: Direction = "Long" if hourly_return > 0 else "Short"
        bias = HourlyBias(
            direction=hourly_direction,
            return_percent=hourly_return,
            effective_lookback=hourly_zone.effective_lookback,
            regime_reset_at=hourly_zone.regime_reset_at,
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
        long_ok = (
            bias.direction == "Long"
            and long_gap <= config.confluence_max_gap_percent
            and bar.low <= long_entry
        )
        short_ok = (
            bias.direction == "Short"
            and short_gap <= config.confluence_max_gap_percent
            and bar.high >= short_entry
        )
        if long_ok == short_ok:
            continue

        if long_ok:
            direction: Direction = "Long"
            entry = long_entry
            gap = long_gap
            fifteen_low, fifteen_high = (
                fifteen_zone.support_bottom,
                fifteen_zone.support_top,
            )
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

        metrics = _metrics_for_signal(direction, entry, five, index, config.horizons_minutes)
        signals.append(
            EntrySignalV2(
                architecture="mtf15_regime",
                symbol=symbol,
                direction=direction,
                entry_at=bar.opened_at,
                entry_price=entry,
                hourly_bias=bias.direction,
                hourly_return_percent=bias.return_percent,
                fifteen_zone_low=fifteen_low,
                fifteen_zone_high=fifteen_high,
                five_zone_low=five_low,
                five_zone_high=five_high,
                zone_gap_percent=gap,
                hourly_effective_lookback=bias.effective_lookback,
                fifteen_effective_lookback=fifteen_zone.effective_lookback,
                five_effective_lookback=five_zone.effective_lookback,
                hourly_regime_reset_at=bias.regime_reset_at,
                fifteen_regime_reset_at=fifteen_zone.regime_reset_at,
                five_regime_reset_at=five_zone.regime_reset_at,
                metrics=metrics,
            )
        )
        cooldown_until_index = index + config.cooldown_bars

    signal_tuple = tuple(signals)
    return EntryResearchV2Result(
        config=config,
        signals=signal_tuple,
        summary=_summary(signal_tuple, days=config.days, horizons=config.horizons_minutes),
        five_minute_fingerprint=_fingerprint(five),
        fifteen_minute_fingerprint=_fingerprint(fifteen),
        hourly_fingerprint=_fingerprint(hours),
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


def _write_v2_signals(path: Path, result: EntryResearchV2Result) -> None:
    metric_names = [
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
    fields = [
        "architecture",
        "symbol",
        "direction",
        "entry_at",
        "entry_price",
        "hourly_bias",
        "hourly_return_percent",
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
        *metric_names,
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for signal in result.signals:
            row: dict[str, Any] = {
                "architecture": signal.architecture,
                "symbol": signal.symbol,
                "direction": signal.direction,
                "entry_at": signal.entry_at.isoformat(),
                "entry_price": signal.entry_price,
                "hourly_bias": signal.hourly_bias,
                "hourly_return_percent": signal.hourly_return_percent,
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
                    signal.five_regime_reset_at.isoformat()
                    if signal.five_regime_reset_at
                    else ""
                ),
            }
            row.update(signal.metrics)
            writer.writerow(row)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_decimal_json) + "\n",
        encoding="utf-8",
    )


def _legacy_summary_with_frequency(summary: dict[str, Any], days: int) -> dict[str, Any]:
    copied = dict(summary)
    signals = int(copied.get("signals", 0))
    copied["signals_per_day"] = round(signals / days, 3) if days else 0.0
    return copied


def _download_dataset(
    config: EntryResearchV2Config,
    *,
    dataset_dir: Path,
) -> tuple[tuple[Candle, ...], tuple[Candle, ...], tuple[Candle, ...], datetime]:
    server_ms = _server_time_ms(config.endpoint)
    server_now = datetime.fromtimestamp(server_ms / 1000, UTC)
    evaluation_start = server_now - timedelta(days=config.days)
    download_start = evaluation_start - timedelta(days=config.warmup_days)
    start_ms = int(download_start.timestamp() * 1000)

    print(
        f"Downloading frozen dataset {config.symbol}: {download_start.isoformat()} .. "
        f"{server_now.isoformat()} from {config.endpoint}"
    )
    datasets: dict[str, tuple[Candle, ...]] = {}
    for interval in ("5", "15", "60"):
        datasets[interval] = tuple(
            item
            for item in download_klines(
                endpoint=config.endpoint,
                symbol=config.symbol,
                interval=interval,
                start_ms=start_ms,
                end_ms=server_ms,
            )
            if int(item.closed_at.timestamp() * 1000) <= server_ms
        )
    five, fifteen, hourly = datasets["5"], datasets["15"], datasets["60"]
    dataset_dir.mkdir(parents=True, exist_ok=True)
    _write_candles(dataset_dir / "trade_5m.csv", five)
    _write_candles(dataset_dir / "trade_15m.csv", fifteen)
    _write_candles(dataset_dir / "trade_60m.csv", hourly)
    _write_json(
        dataset_dir / "dataset_manifest.json",
        {
            "symbol": config.symbol,
            "endpoint": config.endpoint,
            "download_start": download_start,
            "evaluation_start": evaluation_start,
            "server_now": server_now,
            "days": config.days,
            "warmup_days": config.warmup_days,
            "five_minute_fingerprint": _fingerprint(five),
            "fifteen_minute_fingerprint": _fingerprint(fifteen),
            "hourly_fingerprint": _fingerprint(hourly),
            "five_minute_rows": len(five),
            "fifteen_minute_rows": len(fifteen),
            "hourly_rows": len(hourly),
        },
    )
    return five, fifteen, hourly, evaluation_start


def _load_dataset(
    dataset_dir: Path,
    *,
    config: EntryResearchV2Config,
) -> tuple[tuple[Candle, ...], tuple[Candle, ...], tuple[Candle, ...], datetime]:
    manifest_path = dataset_dir / "dataset_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"dataset manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if str(manifest.get("symbol", "")).upper() != config.symbol.upper():
        raise ValueError("dataset symbol does not match requested symbol")
    evaluation_start = datetime.fromisoformat(str(manifest["evaluation_start"]))
    five = _read_candles(dataset_dir / "trade_5m.csv", symbol=config.symbol, timeframe="5")
    fifteen = _read_candles(
        dataset_dir / "trade_15m.csv", symbol=config.symbol, timeframe="15"
    )
    hourly = _read_candles(dataset_dir / "trade_60m.csv", symbol=config.symbol, timeframe="60")
    expected = {
        "five_minute_fingerprint": _fingerprint(five),
        "fifteen_minute_fingerprint": _fingerprint(fifteen),
        "hourly_fingerprint": _fingerprint(hourly),
    }
    for key, actual in expected.items():
        if str(manifest.get(key)) != actual:
            raise ValueError(f"dataset fingerprint mismatch: {key}")
    return five, fifteen, hourly, evaluation_start


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="P29 MTF entry research: frozen data + 1h bias + 15m/5m execution zones"
    )
    parser.add_argument("--symbol", default="UNIUSDT")
    parser.add_argument("--endpoint", default="https://api.bybit.kz")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--warmup-days", type=int, default=14)
    parser.add_argument("--five-lookback", type=int, default=130)
    parser.add_argument("--fifteen-lookback", type=int, default=130)
    parser.add_argument("--hourly-lookback", type=int, default=130)
    parser.add_argument("--atr-period", type=int, default=200)
    parser.add_argument("--zone-half-width-atr", default="0.5")
    parser.add_argument("--confluence-max-gap-percent", default="0.25")
    parser.add_argument("--cooldown-bars", type=int, default=12)
    parser.add_argument("--shock-atr-multiple", default="3.0")
    parser.add_argument("--embargo-minutes-after-shock", type=int, default=60)
    parser.add_argument("--dataset-dir")
    parser.add_argument("--output-dir")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = EntryResearchV2Config(
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
        cooldown_bars=args.cooldown_bars,
        shock_atr_multiple=Decimal(str(args.shock_atr_multiple)),
        embargo_minutes_after_shock=args.embargo_minutes_after_shock,
    )

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("reports") / "entry_research_v2" / f"{config.symbol}_{stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    dataset_dir = Path(args.dataset_dir) if args.dataset_dir else output_dir / "dataset"

    if args.dataset_dir:
        print(f"Loading frozen dataset: {dataset_dir}")
        five, fifteen, hourly, evaluation_start = _load_dataset(dataset_dir, config=config)
    else:
        five, fifteen, hourly, evaluation_start = _download_dataset(
            config, dataset_dir=dataset_dir
        )

    # Baseline P28 adaptive is replayed on the exact same 5m/60m bytes.
    legacy_config = EntryResearchConfig(
        symbol=config.symbol,
        endpoint=config.endpoint,
        days=config.days,
        warmup_days=config.warmup_days,
        five_minute_lookback=config.five_minute_lookback,
        hourly_lookback=config.hourly_lookback,
        atr_period=config.atr_period,
        zone_half_width_atr=config.zone_half_width_atr,
        confluence_max_gap_percent=config.confluence_max_gap_percent,
        cooldown_bars=config.cooldown_bars,
        horizons_minutes=config.horizons_minutes,
        variant="adaptive",
        shock_atr_period=config.shock_atr_period,
        shock_atr_multiple=config.shock_atr_multiple,
        minimum_five_minute_regime_bars=24,
        minimum_hourly_regime_bars=8,
    )
    legacy = run_entry_research(five, hourly, legacy_config, evaluation_start=evaluation_start)
    v2 = run_mtf15_regime_research(
        five,
        fifteen,
        hourly,
        config,
        evaluation_start=evaluation_start,
    )

    legacy_dir = output_dir / "p28_adaptive_same_dataset"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    # Reuse P28's own CSV writer by emitting the result through a tiny local representation.
    from bybit_workbench.research.mtf_entry import _write_signals as _write_legacy_signals

    _write_legacy_signals(legacy_dir / "signals.csv", legacy)
    _write_json(
        legacy_dir / "summary.json",
        {
            "architecture": "p28_adaptive",
            "config": asdict(legacy.config),
            "five_minute_fingerprint": legacy.five_minute_fingerprint,
            "hourly_fingerprint": legacy.hourly_fingerprint,
            "summary": _legacy_summary_with_frequency(legacy.summary, config.days),
        },
    )

    v2_dir = output_dir / "mtf15_regime"
    v2_dir.mkdir(parents=True, exist_ok=True)
    _write_v2_signals(v2_dir / "signals.csv", v2)
    _write_json(
        v2_dir / "summary.json",
        {
            "architecture": "mtf15_regime",
            "config": asdict(config),
            "five_minute_fingerprint": v2.five_minute_fingerprint,
            "fifteen_minute_fingerprint": v2.fifteen_minute_fingerprint,
            "hourly_fingerprint": v2.hourly_fingerprint,
            "summary": v2.summary,
        },
    )

    comparison: dict[str, Any] = {
        "dataset_dir": str(dataset_dir),
        "fingerprints": {
            "5m": v2.five_minute_fingerprint,
            "15m": v2.fifteen_minute_fingerprint,
            "60m": v2.hourly_fingerprint,
        },
        "p28_adaptive": _legacy_summary_with_frequency(legacy.summary, config.days),
        "mtf15_regime": v2.summary,
        "notes": [
            "Both architectures use the exact same frozen 5m/60m bytes.",
            (
                "mtf15_regime uses 1h only as causal direction context; "
                "exact entry location is 15m+5m."
            ),
            "After a detected shock, 5m and 15m zones are embargoed for 60 minutes by default.",
            "Maximum outcome horizon is 360 minutes so five-hour intraday moves are not truncated.",
        ],
    }
    _write_json(output_dir / "comparison.json", comparison)

    print(f"Dataset: {dataset_dir}")
    print(
        "P28 adaptive same dataset: "
        f"signals={legacy.summary.get('signals', 0)} "
        f"signals/day={comparison['p28_adaptive']['signals_per_day']}"
    )
    print(
        "MTF15 regime: "
        f"signals={v2.summary.get('signals', 0)} "
        f"signals/day={v2.summary.get('signals_per_day', 0)}"
    )
    print(f"Report: {output_dir / 'comparison.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
