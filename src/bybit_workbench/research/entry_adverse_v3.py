from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
from array import array
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

from bybit_workbench.domain.models import Candle
from bybit_workbench.research.flow_exhaustion_v2 import MicroTape
from bybit_workbench.research.flow_reversal_v1 import (
    TradeDay,
    _archive_map,
    _combine_trade_days,
    _load_trade_day,
    _required,
)
from bybit_workbench.research.mtf_entry import Direction, _decimal_json
from bybit_workbench.research.mtf_entry_v3 import _read_candles
from bybit_workbench.strategies.indicators import true_ranges

Outcome = Literal["favorable_first", "adverse_first", "neither"]


@dataclass(frozen=True, slots=True)
class AdverseResearchConfig:
    symbol: str = "UNIUSDT"
    horizon_minutes: int = 360
    immediate_minutes: int = 30
    shock_atr_period: int = 20
    shock_atr_multiple: Decimal = Decimal("3.0")
    adverse_thresholds_pct: tuple[Decimal, ...] = (
        Decimal("0.1"),
        Decimal("0.2"),
        Decimal("0.3"),
        Decimal("0.4"),
        Decimal("0.5"),
        Decimal("0.7"),
        Decimal("1.0"),
    )
    favorable_thresholds_pct: tuple[Decimal, ...] = (
        Decimal("0.5"),
        Decimal("1.0"),
    )
    embargo_minutes: tuple[int, ...] = (60, 90)

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        if self.horizon_minutes <= 0:
            raise ValueError("horizon_minutes must be positive")
        if self.immediate_minutes <= 0:
            raise ValueError("immediate_minutes must be positive")
        if self.shock_atr_period <= 0:
            raise ValueError("shock_atr_period must be positive")
        if self.shock_atr_multiple <= 0:
            raise ValueError("shock_atr_multiple must be positive")
        if any(value <= 0 for value in self.adverse_thresholds_pct):
            raise ValueError("adverse thresholds must be positive")
        if any(value <= 0 for value in self.favorable_thresholds_pct):
            raise ValueError("favorable thresholds must be positive")
        if any(value <= 0 for value in self.embargo_minutes):
            raise ValueError("embargo minutes must be positive")


@dataclass(frozen=True, slots=True)
class EntrySignal:
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    entry_price: Decimal
    touch_at: datetime
    hourly_alignment: str
    zone_gap_percent: Decimal
    flow_state: str


@dataclass(frozen=True, slots=True)
class PathResult:
    favorable_hits_seconds: dict[str, float | None]
    adverse_hits_seconds: dict[str, float | None]
    mae_before_plus_0_5_pct: Decimal | None
    mae_before_plus_1_0_pct: Decimal | None
    exact_mae_30m_pct: Decimal | None
    exact_mfe_30m_pct: Decimal | None
    first_0_5_vs_1_0: Outcome
    first_1_0_vs_1_0: Outcome


@dataclass(frozen=True, slots=True)
class ShockInfo:
    first_shock_seconds: float | None
    first_shock_adverse: bool | None
    minus_1_in_shock_candle: bool | None
    shock_before_first_0_5_or_minus_1: bool


@dataclass(frozen=True, slots=True)
class AnalysedSignal:
    source: EntrySignal
    path: PathResult
    shock: ShockInfo


def _optional_decimal(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal: {value}") from exc


def _load_signals(path: Path) -> tuple[EntrySignal, ...]:
    items: list[EntrySignal] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            touch_raw = row.get("touch_at")
            if not touch_raw:
                continue
            direction_raw = _required(row, "direction")
            if direction_raw not in {"Long", "Short"}:
                raise ValueError(f"unsupported direction: {direction_raw}")
            items.append(
                EntrySignal(
                    symbol=_required(row, "symbol"),
                    direction=cast(Direction, direction_raw),
                    candidate_bar_at=datetime.fromisoformat(
                        _required(row, "candidate_bar_at")
                    ).astimezone(UTC),
                    entry_price=Decimal(_required(row, "entry_price")),
                    touch_at=datetime.fromisoformat(touch_raw).astimezone(UTC),
                    hourly_alignment=str(row.get("hourly_alignment") or ""),
                    zone_gap_percent=Decimal(_required(row, "zone_gap_percent")),
                    flow_state=str(row.get("flow_state") or ""),
                )
            )
    return tuple(items)


def _directional_move_pct(direction: Direction, entry_price: float, price: float) -> float:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    raw = (price - entry_price) / entry_price * 100.0
    return raw if direction == "Long" else -raw


def _threshold_key(value: Decimal) -> str:
    return format(value, "f").replace(".", "_")


def _first_outcome(
    favorable_seconds: float | None,
    adverse_seconds: float | None,
) -> Outcome:
    if favorable_seconds is None and adverse_seconds is None:
        return "neither"
    if adverse_seconds is None:
        return "favorable_first"
    if favorable_seconds is None:
        return "adverse_first"
    return "favorable_first" if favorable_seconds <= adverse_seconds else "adverse_first"


def _float64_slice(
    values: Sequence[float],
    start: int,
    end: int,
) -> NDArray[np.float64]:
    count = max(0, end - start)
    if isinstance(values, array) and values.typecode == "d":
        return np.frombuffer(
            values,
            dtype=np.float64,
            count=count,
            offset=start * values.itemsize,
        )
    return np.asarray(values[start:end], dtype=np.float64)


def _first_mask_index(mask: NDArray[np.bool_]) -> int | None:
    if mask.size == 0:
        return None
    index = int(mask.argmax())
    return index if bool(mask[index]) else None


def _analyse_path(
    signal: EntrySignal,
    tape: MicroTape | TradeDay,
    *,
    config: AdverseResearchConfig,
) -> PathResult:
    start_ts = signal.touch_at.timestamp()
    end_ts = start_ts + config.horizon_minutes * 60
    immediate_end_ts = start_ts + config.immediate_minutes * 60
    start_index = bisect.bisect_left(tape.timestamps, start_ts)
    end_index = bisect.bisect_right(tape.timestamps, end_ts)
    entry = float(signal.entry_price)

    favorable_hits: dict[str, float | None] = {
        _threshold_key(value): None for value in config.favorable_thresholds_pct
    }
    adverse_hits: dict[str, float | None] = {
        _threshold_key(value): None for value in config.adverse_thresholds_pct
    }
    if start_index >= end_index:
        return PathResult(
            favorable_hits_seconds=favorable_hits,
            adverse_hits_seconds=adverse_hits,
            mae_before_plus_0_5_pct=None,
            mae_before_plus_1_0_pct=None,
            exact_mae_30m_pct=None,
            exact_mfe_30m_pct=None,
            first_0_5_vs_1_0="neither",
            first_1_0_vs_1_0="neither",
        )

    timestamps = _float64_slice(tape.timestamps, start_index, end_index)
    prices = _float64_slice(tape.prices, start_index, end_index)
    raw_moves = (prices - entry) / entry * 100.0
    moves = raw_moves if signal.direction == "Long" else -raw_moves

    favorable_indices: dict[str, int | None] = {}
    adverse_indices: dict[str, int | None] = {}
    for threshold in config.favorable_thresholds_pct:
        key = _threshold_key(threshold)
        hit_index = _first_mask_index(moves >= float(threshold))
        favorable_indices[key] = hit_index
        if hit_index is not None:
            favorable_hits[key] = float(timestamps[hit_index] - start_ts)
    for threshold in config.adverse_thresholds_pct:
        key = _threshold_key(threshold)
        hit_index = _first_mask_index(moves <= -float(threshold))
        adverse_indices[key] = hit_index
        if hit_index is not None:
            adverse_hits[key] = float(timestamps[hit_index] - start_ts)

    immediate_count = int(np.searchsorted(timestamps, immediate_end_ts, side="right"))
    if immediate_count > 0:
        immediate_moves = moves[:immediate_count]
        mae_30 = float(np.min(immediate_moves))
        mfe_30 = float(np.max(immediate_moves))
    else:
        mae_30 = None
        mfe_30 = None

    def mae_before(target: Decimal) -> Decimal | None:
        key = _threshold_key(target)
        hit_index = favorable_indices[key]
        cutoff = len(moves) if hit_index is None else hit_index + 1
        if cutoff <= 0:
            return None
        return Decimal(str(round(float(np.min(moves[:cutoff])), 8)))

    favorable_0_5 = favorable_hits[_threshold_key(Decimal("0.5"))]
    favorable_1_0 = favorable_hits[_threshold_key(Decimal("1.0"))]
    adverse_1_0 = adverse_hits[_threshold_key(Decimal("1.0"))]
    return PathResult(
        favorable_hits_seconds=favorable_hits,
        adverse_hits_seconds=adverse_hits,
        mae_before_plus_0_5_pct=mae_before(Decimal("0.5")),
        mae_before_plus_1_0_pct=mae_before(Decimal("1.0")),
        exact_mae_30m_pct=(
            None if mae_30 is None else Decimal(str(round(mae_30, 8)))
        ),
        exact_mfe_30m_pct=(
            None if mfe_30 is None else Decimal(str(round(mfe_30, 8)))
        ),
        first_0_5_vs_1_0=_first_outcome(favorable_0_5, adverse_1_0),
        first_1_0_vs_1_0=_first_outcome(favorable_1_0, adverse_1_0),
    )


def _shock_flags(
    candles: tuple[Candle, ...],
    *,
    period: int,
    multiple: Decimal,
) -> tuple[bool, ...]:
    ranges = true_ranges(candles)
    flags = [False] * len(candles)
    if len(candles) <= period:
        return tuple(flags)
    rolling = sum(ranges[:period], Decimal("0"))
    for index in range(period, len(candles)):
        baseline = rolling / Decimal(period)
        flags[index] = baseline > 0 and ranges[index] >= multiple * baseline
        rolling += ranges[index] - ranges[index - period]
    return tuple(flags)


def _candle_index_at(candles: tuple[Candle, ...], timestamp: datetime) -> int | None:
    opened = [item.opened_at.timestamp() for item in candles]
    index = bisect.bisect_right(opened, timestamp.timestamp()) - 1
    if index < 0 or index >= len(candles):
        return None
    if timestamp >= candles[index].closed_at:
        return None
    return index


def _shock_info(
    signal: EntrySignal,
    path: PathResult,
    candles: tuple[Candle, ...],
    flags: tuple[bool, ...],
) -> ShockInfo:
    opened = [item.opened_at.timestamp() for item in candles]
    start_index = bisect.bisect_right(opened, signal.touch_at.timestamp()) - 1
    if start_index < 0:
        start_index = 0
    horizon_end = signal.touch_at + timedelta(hours=6)
    end_index = bisect.bisect_left(opened, horizon_end.timestamp())

    first_shock_index: int | None = None
    for index in range(start_index, min(end_index + 1, len(candles))):
        if flags[index] and candles[index].closed_at > signal.touch_at:
            first_shock_index = index
            break

    first_shock_seconds: float | None = None
    first_shock_adverse: bool | None = None
    if first_shock_index is not None:
        candle = candles[first_shock_index]
        event_at = max(candle.opened_at, signal.touch_at)
        first_shock_seconds = (event_at - signal.touch_at).total_seconds()
        body_move = _directional_move_pct(
            signal.direction,
            float(candle.open),
            float(candle.close),
        )
        first_shock_adverse = body_move < 0

    minus_1_seconds = path.adverse_hits_seconds.get("1_0")
    minus_1_in_shock: bool | None = None
    if minus_1_seconds is not None:
        minus_1_at = signal.touch_at + timedelta(seconds=minus_1_seconds)
        minus_index = _candle_index_at(candles, minus_1_at)
        minus_1_in_shock = bool(minus_index is not None and flags[minus_index])

    plus_0_5_seconds = path.favorable_hits_seconds.get("0_5")
    first_outcome_seconds = _earliest_seconds(plus_0_5_seconds, minus_1_seconds)
    shock_before_outcome = (
        first_shock_seconds is not None
        and first_outcome_seconds is not None
        and first_shock_seconds <= first_outcome_seconds
    )
    return ShockInfo(
        first_shock_seconds=first_shock_seconds,
        first_shock_adverse=first_shock_adverse,
        minus_1_in_shock_candle=minus_1_in_shock,
        shock_before_first_0_5_or_minus_1=shock_before_outcome,
    )


def _earliest_seconds(first: float | None, second: float | None) -> float | None:
    values = [value for value in (first, second) if value is not None]
    return min(values) if values else None


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100.0, 2)


def _median_decimal(values: list[Decimal]) -> float | None:
    if not values:
        return None
    return round(float(statistics.median(values)), 4)


def _quantile(values: list[Decimal], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(float(ordered[0]), 4)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = Decimal(str(position - lower))
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return round(float(value), 4)


def _threshold_rows(
    signals: tuple[AnalysedSignal, ...],
    *,
    config: AdverseResearchConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for target in config.favorable_thresholds_pct:
        target_key = _threshold_key(target)
        target_hit = tuple(
            item for item in signals if item.path.favorable_hits_seconds[target_key] is not None
        )
        for threshold in config.adverse_thresholds_pct:
            adverse_key = _threshold_key(threshold)
            adverse_before = 0
            for item in target_hit:
                favorable_seconds = item.path.favorable_hits_seconds[target_key]
                adverse_seconds = item.path.adverse_hits_seconds[adverse_key]
                if (
                    adverse_seconds is not None
                    and favorable_seconds is not None
                    and adverse_seconds < favorable_seconds
                ):
                    adverse_before += 1
            rows.append(
                {
                    "target_plus_pct": target,
                    "adverse_threshold_pct": threshold,
                    "target_hit_signals": len(target_hit),
                    "adverse_before_target_signals": adverse_before,
                    "adverse_before_target_percent": _percent(adverse_before, len(target_hit)),
                    "target_before_adverse_percent": _percent(
                        len(target_hit) - adverse_before,
                        len(target_hit),
                    ),
                }
            )
    return rows


def _mae_summary(signals: tuple[AnalysedSignal, ...]) -> dict[str, Any]:
    all_30 = [
        -item.path.exact_mae_30m_pct
        for item in signals
        if item.path.exact_mae_30m_pct is not None
    ]
    favorable = tuple(
        item for item in signals if item.path.first_0_5_vs_1_0 == "favorable_first"
    )
    favorable_before = [
        -item.path.mae_before_plus_0_5_pct
        for item in favorable
        if item.path.mae_before_plus_0_5_pct is not None
    ]
    return {
        "all_signals_30m_adverse_magnitude_pct": {
            "median": _median_decimal(all_30),
            "p75": _quantile(all_30, 0.75),
            "p80": _quantile(all_30, 0.80),
            "p90": _quantile(all_30, 0.90),
            "p95": _quantile(all_30, 0.95),
        },
        "plus_0_5_before_minus_1_signals": len(favorable),
        "mae_before_plus_0_5_for_favorable_signals_pct": {
            "median": _median_decimal(favorable_before),
            "p75": _quantile(favorable_before, 0.75),
            "p80": _quantile(favorable_before, 0.80),
            "p90": _quantile(favorable_before, 0.90),
            "p95": _quantile(favorable_before, 0.95),
        },
    }


def _shock_summary(signals: tuple[AnalysedSignal, ...]) -> dict[str, Any]:
    failed = tuple(
        item for item in signals if item.path.first_0_5_vs_1_0 == "adverse_first"
    )
    minus_in_shock = sum(item.shock.minus_1_in_shock_candle is True for item in failed)
    shock_before = sum(item.shock.shock_before_first_0_5_or_minus_1 for item in failed)
    adverse_shock_before = sum(
        item.shock.shock_before_first_0_5_or_minus_1
        and item.shock.first_shock_adverse is True
        for item in failed
    )
    return {
        "adverse_first_signals": len(failed),
        "minus_1_in_shock_candle": minus_in_shock,
        "minus_1_in_shock_candle_percent": _percent(minus_in_shock, len(failed)),
        "any_shock_before_minus_1": shock_before,
        "any_shock_before_minus_1_percent": _percent(shock_before, len(failed)),
        "adverse_shock_before_minus_1": adverse_shock_before,
        "adverse_shock_before_minus_1_percent": _percent(
            adverse_shock_before,
            len(failed),
        ),
    }


def _embargo_simulation(
    signals: tuple[AnalysedSignal, ...],
    minutes: int,
) -> dict[str, Any]:
    ordered = sorted(signals, key=lambda item: item.source.touch_at)
    embargo_until: datetime | None = None
    accepted: list[AnalysedSignal] = []
    blocked = 0
    for item in ordered:
        if embargo_until is not None and item.source.touch_at < embargo_until:
            blocked += 1
            continue
        accepted.append(item)
        if item.path.first_0_5_vs_1_0 != "adverse_first":
            continue
        minus_seconds = item.path.adverse_hits_seconds.get("1_0")
        if minus_seconds is None:
            continue
        failure_at = item.source.touch_at + timedelta(seconds=minus_seconds)
        embargo_until = failure_at + timedelta(minutes=minutes)

    favorable = sum(item.path.first_0_5_vs_1_0 == "favorable_first" for item in accepted)
    adverse = sum(item.path.first_0_5_vs_1_0 == "adverse_first" for item in accepted)
    return {
        "embargo_minutes": minutes,
        "accepted_candidates": len(accepted),
        "blocked_candidates": blocked,
        "accepted_favorable_first": favorable,
        "accepted_adverse_first": adverse,
        "accepted_favorable_percent": _percent(favorable, len(accepted)),
        "accepted_adverse_percent": _percent(adverse, len(accepted)),
        "note": "candidate filter only; not a position/PnL simulation",
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return _decimal_json(value)


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_signals(path: Path, signals: tuple[AnalysedSignal, ...]) -> None:
    base_fields = [
        "symbol",
        "direction",
        "candidate_bar_at",
        "entry_price",
        "touch_at",
        "hourly_alignment",
        "zone_gap_percent",
        "flow_state",
        "mae_before_plus_0_5_pct",
        "mae_before_plus_1_0_pct",
        "exact_mae_30m_pct",
        "exact_mfe_30m_pct",
        "first_0_5_vs_1_0",
        "first_1_0_vs_1_0",
        "first_shock_seconds",
        "first_shock_adverse",
        "minus_1_in_shock_candle",
        "shock_before_first_0_5_or_minus_1",
    ]
    adverse_fields = [f"seconds_to_minus_{_threshold_key(value)}" for value in (
        Decimal("0.1"),
        Decimal("0.2"),
        Decimal("0.3"),
        Decimal("0.4"),
        Decimal("0.5"),
        Decimal("0.7"),
        Decimal("1.0"),
    )]
    favorable_fields = ["seconds_to_plus_0_5", "seconds_to_plus_1_0"]
    fields = base_fields + adverse_fields + favorable_fields
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in signals:
            row: dict[str, Any] = {
                "symbol": item.source.symbol,
                "direction": item.source.direction,
                "candidate_bar_at": item.source.candidate_bar_at.isoformat(),
                "entry_price": item.source.entry_price,
                "touch_at": item.source.touch_at.isoformat(),
                "hourly_alignment": item.source.hourly_alignment,
                "zone_gap_percent": item.source.zone_gap_percent,
                "flow_state": item.source.flow_state,
                "mae_before_plus_0_5_pct": item.path.mae_before_plus_0_5_pct,
                "mae_before_plus_1_0_pct": item.path.mae_before_plus_1_0_pct,
                "exact_mae_30m_pct": item.path.exact_mae_30m_pct,
                "exact_mfe_30m_pct": item.path.exact_mfe_30m_pct,
                "first_0_5_vs_1_0": item.path.first_0_5_vs_1_0,
                "first_1_0_vs_1_0": item.path.first_1_0_vs_1_0,
                "first_shock_seconds": item.shock.first_shock_seconds,
                "first_shock_adverse": item.shock.first_shock_adverse,
                "minus_1_in_shock_candle": item.shock.minus_1_in_shock_candle,
                "shock_before_first_0_5_or_minus_1": (
                    item.shock.shock_before_first_0_5_or_minus_1
                ),
                "seconds_to_plus_0_5": item.path.favorable_hits_seconds.get("0_5"),
                "seconds_to_plus_1_0": item.path.favorable_hits_seconds.get("1_0"),
            }
            for threshold in (
                Decimal("0.1"),
                Decimal("0.2"),
                Decimal("0.3"),
                Decimal("0.4"),
                Decimal("0.5"),
                Decimal("0.7"),
                Decimal("1.0"),
            ):
                key = _threshold_key(threshold)
                row[f"seconds_to_minus_{key}"] = item.path.adverse_hits_seconds.get(key)
            writer.writerow(row)


def _p31_metadata(p31_dir: Path) -> tuple[Path, datetime, datetime]:
    summary_path = p31_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"P31 summary not found: {summary_path}")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    dataset_dir = Path(str(payload["dataset_dir"]))
    evaluation_start = datetime.fromisoformat(str(payload["evaluation_start"])).astimezone(UTC)
    evaluation_end = datetime.fromisoformat(str(payload["evaluation_end"])).astimezone(UTC)
    return dataset_dir, evaluation_start, evaluation_end


def run_adverse_research(
    p31_dir: Path,
    *,
    config: AdverseResearchConfig,
    dataset_dir_override: Path | None = None,
) -> tuple[tuple[AnalysedSignal, ...], Path, datetime, datetime]:
    dataset_dir, evaluation_start, evaluation_end = _p31_metadata(p31_dir)
    if dataset_dir_override is not None:
        dataset_dir = dataset_dir_override
    signals = _load_signals(p31_dir / "signals_touch_exact.csv")
    archives = _archive_map(dataset_dir)
    candles = _read_candles(
        dataset_dir / "trade_5m.csv",
        symbol=config.symbol,
        timeframe="5",
    )
    shock_flags = _shock_flags(
        candles,
        period=config.shock_atr_period,
        multiple=config.shock_atr_multiple,
    )

    by_day: dict[str, list[EntrySignal]] = {}
    for signal in signals:
        by_day.setdefault(signal.touch_at.date().isoformat(), []).append(signal)

    cache: dict[str, TradeDay] = {}
    analysed: list[AnalysedSignal] = []
    ordered_days = sorted(by_day)
    for day_index, day_key in enumerate(ordered_days, start=1):
        print(f"P33 adverse-path day {day_index}/{len(ordered_days)}: {day_key}")
        current_path = archives.get(day_key)
        if current_path is None:
            continue
        if day_key not in cache:
            cache[day_key] = _load_trade_day(
                current_path, progress_label=f"P33 {current_path.name}"
            )
        current = cache[day_key]
        next_key = (datetime.fromisoformat(day_key).date() + timedelta(days=1)).isoformat()
        next_path = archives.get(next_key)
        if next_path is not None and next_key not in cache:
            cache[next_key] = _load_trade_day(
                next_path, progress_label=f"P33 {next_path.name}"
            )
        combined = _combine_trade_days(current, cache.get(next_key))
        for signal in by_day[day_key]:
            path = _analyse_path(signal, combined, config=config)
            analysed.append(
                AnalysedSignal(
                    source=signal,
                    path=path,
                    shock=_shock_info(signal, path, candles, shock_flags),
                )
            )
        previous_key = (datetime.fromisoformat(day_key).date() - timedelta(days=1)).isoformat()
        cache.pop(previous_key, None)

    return (
        tuple(sorted(analysed, key=lambda item: item.source.touch_at)),
        dataset_dir,
        evaluation_start,
        evaluation_end,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P33 adverse excursion and shock research")
    parser.add_argument("--p31-dir", required=True)
    parser.add_argument("--dataset-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--symbol", default="UNIUSDT")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    p31_dir = Path(args.p31_dir).resolve()
    if not p31_dir.exists():
        raise FileNotFoundError(f"P31 result directory not found: {p31_dir}")
    dataset_override = Path(args.dataset_dir).resolve() if args.dataset_dir else None
    config = AdverseResearchConfig(symbol=args.symbol.strip().upper())
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("reports") / "entry_research_v6" / f"{config.symbol}_{stamp}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    signals, dataset_dir, evaluation_start, evaluation_end = run_adverse_research(
        p31_dir,
        config=config,
        dataset_dir_override=dataset_override,
    )
    threshold_rows = _threshold_rows(signals, config=config)
    embargo_rows = [_embargo_simulation(signals, minutes) for minutes in config.embargo_minutes]
    _write_signals(output_dir / "signals_adverse_path.csv", signals)
    _write_rows(output_dir / "threshold_survival.csv", threshold_rows)
    _write_rows(output_dir / "embargo_candidate_filter.csv", embargo_rows)

    outcome_counts = {
        outcome: sum(item.path.first_0_5_vs_1_0 == outcome for item in signals)
        for outcome in ("favorable_first", "adverse_first", "neither")
    }
    summary = {
        "architecture": "p33_adverse_excursion_and_shock",
        "dataset_dir": dataset_dir,
        "p31_dir": p31_dir,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "config": asdict(config),
        "signals": len(signals),
        "outcome_0_5_vs_minus_1": outcome_counts,
        "favorable_0_5_before_minus_1_percent": _percent(
            outcome_counts["favorable_first"],
            len(signals),
        ),
        "mae": _mae_summary(signals),
        "shock": _shock_summary(signals),
        "threshold_survival": threshold_rows,
        "post_failure_embargo_candidate_filter": embargo_rows,
        "notes": [
            "P33 changes no live-trading, stop-loss, take-profit, or exit logic.",
            (
                "A 1% price excursion is studied as structural invalidation, "
                "not as the account risk-budget setting."
            ),
            (
                "Small adverse movement inside a valid zone is expected; P33 measures "
                "how much occurs before +0.5% and +1% targets."
            ),
            (
                "Shock association tests whether -1% failures coincide with the same "
                "causal 5m shock definition used by P30."
            ),
            (
                "Embargo results are candidate-filter diagnostics only and are not a "
                "trade/PnL simulation."
            ),
        ],
    }
    _write_json(output_dir / "summary.json", summary)

    print(f"Dataset: {dataset_dir}")
    print(f"P31 source: {p31_dir}")
    print(f"Signals analysed: {len(signals)}")
    print(f"Report: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
