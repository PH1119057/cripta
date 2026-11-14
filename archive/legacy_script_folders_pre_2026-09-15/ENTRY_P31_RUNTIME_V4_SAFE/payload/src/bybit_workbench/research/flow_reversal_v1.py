from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import io
import json
import statistics
import time
from array import array
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.mtf_entry import Direction, _decimal_json
from bybit_workbench.research.mtf_entry_v3 import (
    EntryResearchV3Config,
    EntrySignalV3,
    FlowBucket,
    _load_dataset,
    _parse_archive_timestamp,
    enrich_with_flow,
    run_local_mtf_research,
)

ExactPairResult = Literal["favorable_first", "adverse_first", "neither", "incomplete"]
FlowState = Literal[
    "pressure_then_reversal",
    "pressure_continues",
    "already_favorable",
    "favorable_then_fades",
    "neutral_or_mixed",
]


@dataclass(frozen=True, slots=True)
class FlowReversalConfig:
    symbol: str = "UNIUSDT"
    exact_horizon_minutes: int = 360
    immediate_mfe_mae_minutes: int = 30
    pressure_minutes: int = 4
    reversal_minutes: int = 1
    pressure_thresholds_pct: tuple[Decimal, ...] = (
        Decimal("0"),
        Decimal("10"),
        Decimal("20"),
        Decimal("30"),
        Decimal("40"),
        Decimal("50"),
    )
    reversal_thresholds_pct: tuple[Decimal, ...] = (
        Decimal("0"),
        Decimal("10"),
        Decimal("20"),
        Decimal("30"),
        Decimal("40"),
        Decimal("50"),
    )

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.exact_horizon_minutes <= 0:
            raise ValueError("exact_horizon_minutes must be positive")
        if self.immediate_mfe_mae_minutes <= 0:
            raise ValueError("immediate_mfe_mae_minutes must be positive")
        if self.pressure_minutes <= 0 or self.reversal_minutes <= 0:
            raise ValueError("flow windows must be positive")
        if any(value < 0 for value in self.pressure_thresholds_pct):
            raise ValueError("pressure thresholds cannot be negative")
        if any(value < 0 for value in self.reversal_thresholds_pct):
            raise ValueError("reversal thresholds cannot be negative")


@dataclass(frozen=True, slots=True)
class TradeDay:
    timestamps: tuple[float, ...]
    prices: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.timestamps) != len(self.prices):
            raise ValueError("trade timestamps/prices length mismatch")


@dataclass(frozen=True, slots=True)
class TouchAnalysis:
    touch_at: datetime | None
    touch_delay_seconds: int | None
    exact_first_0_5_vs_0_5: ExactPairResult
    exact_first_0_5_vs_1_0: ExactPairResult
    seconds_to_plus_0_5: int | None
    seconds_to_minus_0_5: int | None
    seconds_to_minus_1_0: int | None
    exact_mfe_30m_pct: Decimal | None
    exact_mae_30m_pct: Decimal | None
    exact_horizon_complete: bool


@dataclass(frozen=True, slots=True)
class FlowTouchFeatures:
    pressure_directional_delta_pct: Decimal
    reversal_directional_delta_pct: Decimal
    reversal_strength_pct: Decimal
    pressure_total_notional: Decimal
    reversal_total_notional: Decimal
    reversal_notional_vs_pressure_per_minute: Decimal | None
    flow_state: FlowState


@dataclass(frozen=True, slots=True)
class P31Signal:
    base: EntrySignalV3
    touch: TouchAnalysis
    flow: FlowTouchFeatures | None


@dataclass(frozen=True, slots=True)
class P31Result:
    config: FlowReversalConfig
    base_config: EntryResearchV3Config
    dataset_dir: Path
    evaluation_start: datetime
    evaluation_end: datetime
    signals: tuple[P31Signal, ...]
    summary: dict[str, Any] = field(default_factory=dict)


def _required(row: dict[str, str | None], key: str) -> str:
    value = row.get(key)
    if value is None or value == "":
        raise ValueError(f"missing CSV field: {key}")
    return value


def _required_cell(row: list[str], index: int, key: str) -> str:
    if index >= len(row):
        raise ValueError(f"missing CSV field: {key}")
    value = row[index]
    if value == "":
        raise ValueError(f"missing CSV field: {key}")
    return value


def _format_progress_duration(seconds: float) -> str:
    safe = max(0, int(round(seconds)))
    hours, remainder = divmod(safe, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _load_trade_day(
    path: Path,
    *,
    progress_label: str | None = None,
    heartbeat_seconds: float = 20.0,
    progress_sink: Callable[[str], None] | None = None,
) -> TradeDay:
    timestamps: array[float] = array("d")
    prices: array[float] = array("d")
    total_bytes = max(1, path.stat().st_size)
    started = time.monotonic()
    last_report = started
    rows = 0

    with path.open("rb") as raw_handle:

        def report(*, final: bool = False) -> None:
            nonlocal last_report
            if progress_label is None:
                return
            now = time.monotonic()
            if not final and now - last_report < heartbeat_seconds:
                return
            elapsed = max(0.0, now - started)
            try:
                compressed_bytes = int(raw_handle.tell())
            except (OSError, ValueError):
                compressed_bytes = 0
            percent = min(100.0, compressed_bytes * 100.0 / total_bytes)
            eta_text = "unknown"
            if compressed_bytes > 0 and elapsed > 0 and compressed_bytes < total_bytes:
                remaining = elapsed * (total_bytes - compressed_bytes) / compressed_bytes
                eta_text = _format_progress_duration(remaining)
            elif compressed_bytes >= total_bytes:
                eta_text = "00:00:00"
            message = (
                f"[P31 tape] file={progress_label} rows={rows:,} "
                f"compressed={compressed_bytes}/{total_bytes} ({percent:.1f}%) "
                f"elapsed={_format_progress_duration(elapsed)} ETA~{eta_text}"
            )
            if progress_sink is None:
                print(message, flush=True)
            else:
                progress_sink(message)
            last_report = now

        with gzip.GzipFile(fileobj=raw_handle, mode="rb") as compressed_handle:
            with io.TextIOWrapper(
                compressed_handle,
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                reader = csv.reader(handle)
                try:
                    fieldnames = next(reader)
                except StopIteration as exc:
                    raise ValueError(f"trade archive has no header: {path}") from exc
                names = {
                    name.strip().lower(): index
                    for index, name in enumerate(fieldnames)
                    if name
                }
                required = {"timestamp", "price"}
                if not required.issubset(names):
                    raise ValueError(
                        f"unsupported trade archive header in {path.name}: {fieldnames}"
                    )
                timestamp_index = names["timestamp"]
                price_index = names["price"]
                for row in reader:
                    try:
                        traded_at = _parse_archive_timestamp(
                            _required_cell(row, timestamp_index, "timestamp")
                        )
                        price = float(Decimal(_required_cell(row, price_index, "price")))
                    except (InvalidOperation, ValueError, TypeError) as exc:
                        raise ValueError(f"invalid trade row in {path.name}: {row}") from exc
                    timestamps.append(traded_at.timestamp())
                    prices.append(price)
                    rows += 1
                    if rows % 10_000 == 0:
                        report()
                report(final=True)

    if any(left > right for left, right in zip(timestamps, timestamps[1:], strict=False)):
        ordered = sorted(zip(timestamps, prices, strict=True), key=lambda item: item[0])
        timestamps = array("d", (item[0] for item in ordered))
        prices = array("d", (item[1] for item in ordered))
    # Runtime uses packed doubles to keep BTC raw-tape memory bounded.  TradeDay stays
    # tuple-annotated for compatibility with existing strict-mypy research consumers;
    # array('d') supports every sequence operation used by those consumers.
    return TradeDay(
        cast(tuple[float, ...], timestamps),
        cast(tuple[float, ...], prices),
    )


def _archive_map(dataset_dir: Path) -> dict[str, Path]:
    manifest_path = dataset_dir / "dataset_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"dataset manifest not found: {manifest_path}")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_archives = payload.get("public_trade_archives")
    if not isinstance(raw_archives, dict) or not raw_archives:
        raise ValueError("dataset manifest has no public_trade_archives")
    root = dataset_dir / "public_trades"
    result: dict[str, Path] = {}
    for raw_name in raw_archives:
        name = str(raw_name)
        path = root / name
        if not path.exists():
            raise FileNotFoundError(f"public trade archive is missing: {path}")
        date_part = name.removeprefix(payload.get("symbol", "")).removesuffix(".csv.gz")
        result[date_part] = path
    return result


def _combine_trade_days(first: TradeDay, second: TradeDay | None) -> TradeDay:
    if second is None:
        return first
    timestamps: array[float] = array("d", first.timestamps)
    timestamps.extend(second.timestamps)
    prices: array[float] = array("d", first.prices)
    prices.extend(second.prices)
    return TradeDay(
        cast(tuple[float, ...], timestamps),
        cast(tuple[float, ...], prices),
    )


def _find_touch_index(
    timestamps: tuple[float, ...],
    prices: tuple[float, ...],
    *,
    direction: Direction,
    entry_price: float,
    window_start: float,
    window_end: float,
) -> int | None:
    start = bisect.bisect_left(timestamps, window_start)
    end = bisect.bisect_left(timestamps, window_end)
    for index in range(start, end):
        price = prices[index]
        if direction == "Long" and price <= entry_price:
            return index
        if direction == "Short" and price >= entry_price:
            return index
    return None


def _directional_return_pct(direction: Direction, entry: float, price: float) -> float:
    if entry <= 0:
        raise ValueError("entry price must be positive")
    raw = (price - entry) / entry * 100.0
    return raw if direction == "Long" else -raw


def _pair_result(
    favorable_at: float | None,
    adverse_at: float | None,
    *,
    complete: bool,
) -> ExactPairResult:
    if favorable_at is not None and adverse_at is not None:
        return "favorable_first" if favorable_at <= adverse_at else "adverse_first"
    if favorable_at is not None:
        return "favorable_first"
    if adverse_at is not None:
        return "adverse_first"
    return "neither" if complete else "incomplete"


def _analyse_touch(
    signal: EntrySignalV3,
    trades: TradeDay,
    *,
    config: FlowReversalConfig,
    data_end: datetime,
) -> TouchAnalysis:
    entry_start = signal.entry_at.timestamp()
    entry_end = (signal.entry_at + timedelta(minutes=5)).timestamp()
    touch_index = _find_touch_index(
        trades.timestamps,
        trades.prices,
        direction=signal.direction,
        entry_price=float(signal.entry_price),
        window_start=entry_start,
        window_end=entry_end,
    )
    if touch_index is None:
        return TouchAnalysis(
            touch_at=None,
            touch_delay_seconds=None,
            exact_first_0_5_vs_0_5="incomplete",
            exact_first_0_5_vs_1_0="incomplete",
            seconds_to_plus_0_5=None,
            seconds_to_minus_0_5=None,
            seconds_to_minus_1_0=None,
            exact_mfe_30m_pct=None,
            exact_mae_30m_pct=None,
            exact_horizon_complete=False,
        )

    touch_ts = trades.timestamps[touch_index]
    touch_at = datetime.fromtimestamp(touch_ts, UTC)
    exact_end = touch_at + timedelta(minutes=config.exact_horizon_minutes)
    data_end_ts = data_end.timestamp()
    scan_end_ts = min(exact_end.timestamp(), data_end_ts)
    scan_end = bisect.bisect_left(trades.timestamps, scan_end_ts)
    complete = exact_end <= data_end

    plus_0_5_at: float | None = None
    minus_0_5_at: float | None = None
    minus_1_0_at: float | None = None
    mfe_30 = float("-inf")
    mae_30 = float("inf")
    immediate_end_ts = (
        touch_at + timedelta(minutes=config.immediate_mfe_mae_minutes)
    ).timestamp()

    for index in range(touch_index, scan_end):
        traded_at = trades.timestamps[index]
        directional = _directional_return_pct(
            signal.direction,
            float(signal.entry_price),
            trades.prices[index],
        )
        if traded_at <= immediate_end_ts:
            mfe_30 = max(mfe_30, directional)
            mae_30 = min(mae_30, directional)
        if plus_0_5_at is None and directional >= 0.5:
            plus_0_5_at = traded_at
        if minus_0_5_at is None and directional <= -0.5:
            minus_0_5_at = traded_at
        if minus_1_0_at is None and directional <= -1.0:
            minus_1_0_at = traded_at
        if (
            plus_0_5_at is not None
            and minus_0_5_at is not None
            and minus_1_0_at is not None
            and traded_at > immediate_end_ts
        ):
            break

    def elapsed(hit_at: float | None) -> int | None:
        return None if hit_at is None else max(0, int(round(hit_at - touch_ts)))

    return TouchAnalysis(
        touch_at=touch_at,
        touch_delay_seconds=max(0, int(round(touch_ts - entry_start))),
        exact_first_0_5_vs_0_5=_pair_result(plus_0_5_at, minus_0_5_at, complete=complete),
        exact_first_0_5_vs_1_0=_pair_result(plus_0_5_at, minus_1_0_at, complete=complete),
        seconds_to_plus_0_5=elapsed(plus_0_5_at),
        seconds_to_minus_0_5=elapsed(minus_0_5_at),
        seconds_to_minus_1_0=elapsed(minus_1_0_at),
        exact_mfe_30m_pct=(
            None if mfe_30 == float("-inf") else Decimal(str(round(mfe_30, 8)))
        ),
        exact_mae_30m_pct=(
            None if mae_30 == float("inf") else Decimal(str(round(mae_30, 8)))
        ),
        exact_horizon_complete=complete,
    )


def _window_flow(
    mapping: dict[datetime, FlowBucket],
    *,
    end: datetime,
    minutes: int,
    offset_minutes: int = 0,
) -> tuple[Decimal, Decimal]:
    buy = Decimal("0")
    sell = Decimal("0")
    normalized_end = end.replace(second=0, microsecond=0)
    for offset in range(offset_minutes, offset_minutes + minutes):
        minute = normalized_end - timedelta(minutes=offset + 1)
        bucket = mapping.get(minute)
        if bucket is None:
            continue
        buy += bucket.buy_notional
        sell += bucket.sell_notional
    return buy, sell


def _directional_delta_pct(direction: Direction, buy: Decimal, sell: Decimal) -> Decimal:
    total = buy + sell
    if total <= 0:
        return Decimal("0")
    raw = (buy - sell) / total * Decimal("100")
    return raw if direction == "Long" else -raw


def _flow_state(pressure: Decimal, reversal: Decimal) -> FlowState:
    if pressure < 0 and reversal > 0:
        return "pressure_then_reversal"
    if pressure < 0 and reversal <= 0:
        return "pressure_continues"
    if pressure >= 0 and reversal > 0:
        return "already_favorable"
    if pressure > 0 and reversal < 0:
        return "favorable_then_fades"
    return "neutral_or_mixed"


def _flow_features_for_touch(
    signal: EntrySignalV3,
    touch_at: datetime,
    mapping: dict[datetime, FlowBucket],
    *,
    config: FlowReversalConfig,
) -> FlowTouchFeatures:
    reversal_buy, reversal_sell = _window_flow(
        mapping,
        end=touch_at,
        minutes=config.reversal_minutes,
    )
    pressure_buy, pressure_sell = _window_flow(
        mapping,
        end=touch_at,
        minutes=config.pressure_minutes,
        offset_minutes=config.reversal_minutes,
    )
    pressure = _directional_delta_pct(signal.direction, pressure_buy, pressure_sell)
    reversal = _directional_delta_pct(signal.direction, reversal_buy, reversal_sell)
    pressure_total = pressure_buy + pressure_sell
    reversal_total = reversal_buy + reversal_sell
    pressure_per_minute = pressure_total / Decimal(config.pressure_minutes)
    ratio = (
        None
        if pressure_per_minute <= 0
        else reversal_total / pressure_per_minute
    )
    return FlowTouchFeatures(
        pressure_directional_delta_pct=pressure,
        reversal_directional_delta_pct=reversal,
        reversal_strength_pct=reversal - pressure,
        pressure_total_notional=pressure_total,
        reversal_total_notional=reversal_total,
        reversal_notional_vs_pressure_per_minute=ratio,
        flow_state=_flow_state(pressure, reversal),
    )


def _safe_median(values: list[Decimal]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 4)


def _percentage(count: int, total: int) -> float:
    return 0.0 if total <= 0 else round(count * 100.0 / total, 2)


def _signal_summary(signals: tuple[P31Signal, ...]) -> dict[str, Any]:
    touched = tuple(item for item in signals if item.touch.touch_at is not None)
    resolved_05_10 = tuple(
        item
        for item in touched
        if item.touch.exact_first_0_5_vs_1_0 not in {"incomplete"}
    )
    favorable_05_10 = len(
        [
            item
            for item in resolved_05_10
            if item.touch.exact_first_0_5_vs_1_0 == "favorable_first"
        ]
    )
    resolved_05_05 = tuple(
        item
        for item in touched
        if item.touch.exact_first_0_5_vs_0_5 not in {"incomplete"}
    )
    favorable_05_05 = len(
        [
            item
            for item in resolved_05_05
            if item.touch.exact_first_0_5_vs_0_5 == "favorable_first"
        ]
    )
    mfe_values = [
        item.touch.exact_mfe_30m_pct
        for item in touched
        if item.touch.exact_mfe_30m_pct is not None
    ]
    mae_values = [
        item.touch.exact_mae_30m_pct
        for item in touched
        if item.touch.exact_mae_30m_pct is not None
    ]
    delays = [
        item.touch.touch_delay_seconds
        for item in touched
        if item.touch.touch_delay_seconds is not None
    ]
    states: dict[str, int] = {}
    for item in touched:
        if item.flow is None:
            continue
        states[item.flow.flow_state] = states.get(item.flow.flow_state, 0) + 1
    return {
        "signals": len(signals),
        "touched": len(touched),
        "touch_rate_percent": _percentage(len(touched), len(signals)),
        "exact_first_0_5_vs_1_0_favorable_percent": _percentage(
            favorable_05_10, len(resolved_05_10)
        ),
        "exact_first_0_5_vs_0_5_favorable_percent": _percentage(
            favorable_05_05, len(resolved_05_05)
        ),
        "exact_pair_0_5_vs_1_0_resolved": len(resolved_05_10),
        "exact_pair_0_5_vs_0_5_resolved": len(resolved_05_05),
        "median_exact_mfe_30m_pct": _safe_median(mfe_values),
        "median_exact_mae_30m_pct": _safe_median(mae_values),
        "median_touch_delay_seconds": (
            None if not delays else round(float(statistics.median(delays)), 1)
        ),
        "flow_states": states,
    }


def _subset_summary(signals: tuple[P31Signal, ...]) -> dict[str, Any]:
    summary = _signal_summary(signals)
    summary["long"] = len([item for item in signals if item.base.direction == "Long"])
    summary["short"] = len([item for item in signals if item.base.direction == "Short"])
    return summary


def _threshold_matrix(result: P31Result) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    usable = tuple(item for item in result.signals if item.flow is not None)
    for pressure_threshold in result.config.pressure_thresholds_pct:
        for reversal_threshold in result.config.reversal_thresholds_pct:
            subset = tuple(
                item
                for item in usable
                if item.flow is not None
                and item.flow.pressure_directional_delta_pct <= -pressure_threshold
                and item.flow.reversal_directional_delta_pct >= reversal_threshold
            )
            summary = _subset_summary(subset)
            rows.append(
                {
                    "pressure_at_least_pct": pressure_threshold,
                    "reversal_at_least_pct": reversal_threshold,
                    **summary,
                }
            )
    return rows


def _flow_state_summaries(signals: tuple[P31Signal, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    states: tuple[FlowState, ...] = (
        "pressure_then_reversal",
        "pressure_continues",
        "already_favorable",
        "favorable_then_fades",
        "neutral_or_mixed",
    )
    for state in states:
        subset = tuple(
            item
            for item in signals
            if item.flow is not None and item.flow.flow_state == state
        )
        result[state] = _subset_summary(subset)
    return result


def _monthly_slices(
    signals: tuple[P31Signal, ...],
    *,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = evaluation_start
    index = 1
    while start < evaluation_end:
        end = min(start + timedelta(days=30), evaluation_end)
        subset = tuple(item for item in signals if start <= item.base.entry_at < end)
        flow_flip = tuple(
            item
            for item in subset
            if item.flow is not None and item.flow.flow_state == "pressure_then_reversal"
        )
        rows.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "all": _subset_summary(subset),
                "pressure_then_reversal": _subset_summary(flow_flip),
            }
        )
        start = end
        index += 1
    return rows


def _direction_summaries(signals: tuple[P31Signal, ...]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for direction in ("Long", "Short"):
        subset = tuple(item for item in signals if item.base.direction == direction)
        flow_flip = tuple(
            item
            for item in subset
            if item.flow is not None and item.flow.flow_state == "pressure_then_reversal"
        )
        result[direction] = {
            "all": _subset_summary(subset),
            "pressure_then_reversal": _subset_summary(flow_flip),
        }
    return result


def _p30_vs_exact(signals: tuple[P31Signal, ...]) -> dict[str, Any]:
    comparable = tuple(
        item
        for item in signals
        if item.touch.touch_at is not None
        and item.touch.exact_first_0_5_vs_1_0 not in {"incomplete"}
    )
    same = 0
    different = 0
    p30_favorable = 0
    exact_favorable = 0
    for item in comparable:
        p30 = str(item.base.outcome_metrics.get("first_0_5_vs_1_0"))
        exact = item.touch.exact_first_0_5_vs_1_0
        p30_favorable += p30 == "favorable_first"
        exact_favorable += exact == "favorable_first"
        if p30 == exact:
            same += 1
        else:
            different += 1
    return {
        "comparable_signals": len(comparable),
        "same_result": same,
        "different_result": different,
        "different_percent": _percentage(different, len(comparable)),
        "p30_favorable_percent": _percentage(p30_favorable, len(comparable)),
        "exact_favorable_percent": _percentage(exact_favorable, len(comparable)),
        "note": (
            "P30 uses 5-minute OHLC and cannot know whether a threshold happened before the "
            "limit entry touched inside the same candle. P31 resolves touch and threshold order "
            "from the raw public-trade tape."
        ),
    }


def _write_signals(path: Path, result: P31Result) -> None:
    base_fields = [
        "symbol",
        "direction",
        "candidate_bar_at",
        "entry_price",
        "hourly_context",
        "hourly_alignment",
        "zone_gap_percent",
        "p30_first_0_5_vs_1_0",
        "p30_hit_plus_0_5_pct",
        "p30_hit_plus_1_pct",
    ]
    touch_fields = [
        "touch_at",
        "touch_delay_seconds",
        "exact_first_0_5_vs_0_5",
        "exact_first_0_5_vs_1_0",
        "seconds_to_plus_0_5",
        "seconds_to_minus_0_5",
        "seconds_to_minus_1_0",
        "exact_mfe_30m_pct",
        "exact_mae_30m_pct",
        "exact_horizon_complete",
    ]
    flow_fields = [
        "flow_state",
        "pressure_directional_delta_pct",
        "reversal_directional_delta_pct",
        "reversal_strength_pct",
        "pressure_total_notional",
        "reversal_total_notional",
        "reversal_notional_vs_pressure_per_minute",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=base_fields + touch_fields + flow_fields)
        writer.writeheader()
        for item in result.signals:
            flow = item.flow
            writer.writerow(
                {
                    "symbol": item.base.symbol,
                    "direction": item.base.direction,
                    "candidate_bar_at": item.base.entry_at.isoformat(),
                    "entry_price": item.base.entry_price,
                    "hourly_context": item.base.hourly_context,
                    "hourly_alignment": item.base.hourly_alignment,
                    "zone_gap_percent": item.base.zone_gap_percent,
                    "p30_first_0_5_vs_1_0": item.base.outcome_metrics.get("first_0_5_vs_1_0"),
                    "p30_hit_plus_0_5_pct": item.base.outcome_metrics.get("hit_plus_0_5_pct"),
                    "p30_hit_plus_1_pct": item.base.outcome_metrics.get("hit_plus_1_pct"),
                    "touch_at": item.touch.touch_at.isoformat() if item.touch.touch_at else "",
                    "touch_delay_seconds": item.touch.touch_delay_seconds,
                    "exact_first_0_5_vs_0_5": item.touch.exact_first_0_5_vs_0_5,
                    "exact_first_0_5_vs_1_0": item.touch.exact_first_0_5_vs_1_0,
                    "seconds_to_plus_0_5": item.touch.seconds_to_plus_0_5,
                    "seconds_to_minus_0_5": item.touch.seconds_to_minus_0_5,
                    "seconds_to_minus_1_0": item.touch.seconds_to_minus_1_0,
                    "exact_mfe_30m_pct": item.touch.exact_mfe_30m_pct,
                    "exact_mae_30m_pct": item.touch.exact_mae_30m_pct,
                    "exact_horizon_complete": int(item.touch.exact_horizon_complete),
                    "flow_state": flow.flow_state if flow else "",
                    "pressure_directional_delta_pct": (
                        flow.pressure_directional_delta_pct if flow else ""
                    ),
                    "reversal_directional_delta_pct": (
                        flow.reversal_directional_delta_pct if flow else ""
                    ),
                    "reversal_strength_pct": flow.reversal_strength_pct if flow else "",
                    "pressure_total_notional": flow.pressure_total_notional if flow else "",
                    "reversal_total_notional": flow.reversal_total_notional if flow else "",
                    "reversal_notional_vs_pressure_per_minute": (
                        flow.reversal_notional_vs_pressure_per_minute if flow else ""
                    ),
                }
            )


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return _decimal_json(value)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _write_threshold_matrix(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flattened = dict(row)
            flow_states = flattened.pop("flow_states", None)
            if flow_states is not None:
                flattened["flow_states"] = json.dumps(flow_states, ensure_ascii=False)
            writer.writerow(flattened)


def _base_config_from_manifest(dataset_dir: Path) -> EntryResearchV3Config:
    manifest_path = dataset_dir / "dataset_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return EntryResearchV3Config(
        symbol=str(payload.get("symbol", "UNIUSDT")),
        endpoint=str(payload.get("endpoint", "https://api.bybit.kz")),
        days=int(payload.get("days", 90)),
        warmup_days=int(payload.get("warmup_days", 14)),
    )


def run_flow_reversal_research(
    dataset_dir: Path,
    *,
    config: FlowReversalConfig,
) -> P31Result:
    base_config = _base_config_from_manifest(dataset_dir)
    five, fifteen, hourly, flow, evaluation_start, evaluation_end = _load_dataset(
        dataset_dir, config=base_config
    )
    base_result = run_local_mtf_research(
        five,
        fifteen,
        hourly,
        base_config,
        evaluation_start=evaluation_start,
    )
    base_result = enrich_with_flow(base_result, flow)
    flow_mapping = {bucket.opened_at: bucket for bucket in flow}
    archives = _archive_map(dataset_dir)

    by_day: dict[str, list[EntrySignalV3]] = {}
    for signal in base_result.signals:
        by_day.setdefault(signal.entry_at.date().isoformat(), []).append(signal)

    analysed: list[P31Signal] = []
    cache: dict[str, TradeDay] = {}
    ordered_days = sorted(by_day)
    total_days = len(ordered_days)
    days_started = time.monotonic()
    for day_index, day_key in enumerate(ordered_days, start=1):
        print(
            f"[P31 day] current={day_index}/{total_days} ({day_index * 100.0 / total_days:.1f}%) "
            f"date={day_key}",
            flush=True,
        )
        current_path = archives.get(day_key)
        if current_path is None:
            for signal in by_day[day_key]:
                touch = TouchAnalysis(
                    touch_at=None,
                    touch_delay_seconds=None,
                    exact_first_0_5_vs_0_5="incomplete",
                    exact_first_0_5_vs_1_0="incomplete",
                    seconds_to_plus_0_5=None,
                    seconds_to_minus_0_5=None,
                    seconds_to_minus_1_0=None,
                    exact_mfe_30m_pct=None,
                    exact_mae_30m_pct=None,
                    exact_horizon_complete=False,
                )
                analysed.append(P31Signal(base=signal, touch=touch, flow=None))
            continue
        if day_key not in cache:
            cache[day_key] = _load_trade_day(
                current_path,
                progress_label=current_path.name,
            )
        current = cache[day_key]
        next_day_key = (datetime.fromisoformat(day_key).date() + timedelta(days=1)).isoformat()
        next_path = archives.get(next_day_key)
        if next_path is not None and next_day_key not in cache:
            cache[next_day_key] = _load_trade_day(
                next_path,
                progress_label=next_path.name,
            )
        combined = _combine_trade_days(current, cache.get(next_day_key))

        for signal in by_day[day_key]:
            touch = _analyse_touch(
                signal,
                combined,
                config=config,
                data_end=evaluation_end,
            )
            features = (
                None
                if touch.touch_at is None
                else _flow_features_for_touch(
                    signal,
                    touch.touch_at,
                    flow_mapping,
                    config=config,
                )
            )
            analysed.append(P31Signal(base=signal, touch=touch, flow=features))

        previous_day_key = (
            datetime.fromisoformat(day_key).date() - timedelta(days=1)
        ).isoformat()
        cache.pop(previous_day_key, None)

        elapsed_days = time.monotonic() - days_started
        completed_days = day_index
        eta_seconds = (
            elapsed_days * (total_days - completed_days) / completed_days
            if completed_days > 0
            else 0.0
        )
        print(
            f"[P31 day] completed={completed_days}/{total_days} "
            f"({completed_days * 100.0 / total_days:.1f}%) "
            f"elapsed={_format_progress_duration(elapsed_days)} "
            f"ETA~{_format_progress_duration(eta_seconds)}",
            flush=True,
        )

    signal_tuple = tuple(sorted(analysed, key=lambda item: item.base.entry_at))
    result = P31Result(
        config=config,
        base_config=base_config,
        dataset_dir=dataset_dir,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        signals=signal_tuple,
    )
    summary = {
        "all": _subset_summary(signal_tuple),
        "flow_states": _flow_state_summaries(signal_tuple),
        "by_direction": _direction_summaries(signal_tuple),
        "thirty_day_slices": _monthly_slices(
            signal_tuple,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
        ),
        "p30_vs_p31_exact": _p30_vs_exact(signal_tuple),
    }
    return P31Result(
        config=result.config,
        base_config=result.base_config,
        dataset_dir=result.dataset_dir,
        evaluation_start=result.evaluation_start,
        evaluation_end=result.evaluation_end,
        signals=result.signals,
        summary=summary,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P31 touch-aligned flow reversal research")
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--symbol", default="UNIUSDT")
    parser.add_argument("--exact-horizon-minutes", type=int, default=360)
    parser.add_argument("--immediate-minutes", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    dataset_dir = Path(args.dataset_dir).resolve()
    if not dataset_dir.exists():
        raise FileNotFoundError(f"dataset directory not found: {dataset_dir}")
    config = FlowReversalConfig(
        symbol=args.symbol.strip().upper(),
        exact_horizon_minutes=args.exact_horizon_minutes,
        immediate_mfe_mae_minutes=args.immediate_minutes,
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("reports") / "entry_research_v4" / f"{config.symbol}_{stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    result = run_flow_reversal_research(dataset_dir, config=config)
    _write_signals(output_dir / "signals_touch_exact.csv", result)
    matrix = _threshold_matrix(result)
    _write_threshold_matrix(output_dir / "threshold_matrix.csv", matrix)
    _write_json(
        output_dir / "summary.json",
        {
            "architecture": "p31_touch_aligned_flow_reversal",
            "dataset_dir": result.dataset_dir,
            "evaluation_start": result.evaluation_start,
            "evaluation_end": result.evaluation_end,
            "config": asdict(result.config),
            "base_p30_config": asdict(result.base_config),
            "summary": result.summary,
            "threshold_matrix": matrix,
            "notes": [
                "No live-trading or exit logic is changed by P31.",
                "The raw trade tape resolves the actual first touch inside the "
                "5-minute candidate bar.",
                "Flow windows use only fully completed minutes strictly before the touch minute.",
                "The exact +0.5/-0.5 and +0.5/-1.0 ordering is resolved from "
                "public trades after touch.",
                "Threshold matrix is diagnostic only; P31 does not promote any threshold "
                "into a trading gate.",
            ],
        },
    )
    print(f"Dataset: {dataset_dir}")
    print(f"Signals: {len(result.signals)}")
    all_summary = result.summary["all"]
    print(
        "Exact timing +0.5 before -1.0: "
        f"{all_summary['exact_first_0_5_vs_1_0_favorable_percent']}%"
    )
    flip_summary = result.summary["flow_states"]["pressure_then_reversal"]
    print(
        "Pressure -> reversal: "
        f"signals={flip_summary['signals']} "
        "+0.5 before -1.0="
        f"{flip_summary['exact_first_0_5_vs_1_0_favorable_percent']}%"
    )
    print(f"Report: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
