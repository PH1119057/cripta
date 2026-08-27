from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import time
from array import array
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
from numpy.typing import NDArray

from bybit_workbench.research.entry_offset_adverse_eo1 import (
    ALL_SYMBOLS,
    PERIOD_TAG,
    discover_sources,
)
from bybit_workbench.research.entry_offset_no_floor_eo2 import (
    EXPECTED_FILLED_0P20,
    FROZEN_END,
    SOURCE_EVENT_SHA256,
    SourceFill,
    load_source_fills,
)
from bybit_workbench.research.exit_break_even_v13 import TradeDayCache
from bybit_workbench.research.flow_reversal_v1 import TradeDay, _archive_map, _load_trade_day
from bybit_workbench.research.mtf_entry import Direction

RESEARCH_VERSION = "EO3_FULL_PATH_MFE_GIVEBACK_RETEST_ANATOMY_V1"
ENGINE_REVISION = "EO3_1M_CACHE_RAW_BOUNDARY_V1"
CACHE_VERSION = "EO3_1M_TRADE_CACHE_V1"
INITIAL_STOP_PCT = 1.0
MILESTONES_PCT = (0.10, 0.20, 0.30, 0.50, 0.75, 1.00, 1.10, 1.50, 2.00, 3.00, 5.00, 10.00, 20.00)
ACTIVATION_LEVELS_PCT = (0.30, 0.50, 0.75, 1.00, 1.10, 1.50, 2.00, 3.00, 5.00)
GIVEBACK_LEVELS_PCT = (0.10, 0.20, 0.30, 0.50, 0.75, 1.00, 1.50, 2.00)
ADVERSE_LEVELS_PCT = (0.10, 0.20, 0.30, 0.50, 0.75, 1.00)
HORIZONS_HOURS = (1, 3, 6, 12, 24, 48, 72)
POST_STOP_HOURS = 72
MIN_GIVEBACK_ACTIVATION_PCT = 0.30

EndReason = Literal["initial_stop", "data_end_open"]


@dataclass(frozen=True, slots=True)
class MinuteDay:
    minute_ts: NDArray[np.float64]
    high: NDArray[np.float64]
    low: NDArray[np.float64]
    close: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class MinuteSeries:
    minute_ts: NDArray[np.float64]
    high: NDArray[np.float64]
    low: NDArray[np.float64]
    close: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class SourceSignal:
    symbol: str
    direction: Direction
    touch_at: datetime
    filled_0p20: bool
    fill_at_0p20: datetime | None


@dataclass(frozen=True, slots=True)
class TradeAnatomy:
    symbol: str
    direction: Direction
    touch_at: datetime
    fill_at: datetime
    fill_price: float
    end_reason: EndReason
    end_at: datetime
    duration_hours: float
    mfe_pct: float
    mae_pct: float
    mfe_at_minute: datetime
    max_milestone_pct: float
    max_close_giveback_from_running_mfe_pct: float
    returned_to_entry_after_plus_0p30: bool
    returned_to_entry_after_plus_0p50: bool
    returned_to_entry_after_plus_0p75: bool
    returned_to_entry_after_plus_1p00: bool
    returned_to_entry_after_plus_1p10: bool
    hard_stop_after_plus_0p50: bool
    hard_stop_after_plus_0p75: bool
    hard_stop_after_plus_1p00: bool
    hard_stop_after_plus_1p10: bool
    post_stop_72h_mfe_pct: float | None
    post_stop_72h_returned_to_entry: bool | None
    post_stop_72h_reached_plus_0p50: bool | None
    post_stop_72h_reached_plus_1p10: bool | None
    overlapping_signals: int
    overlapping_0p20_fills: int
    first_overlapping_signal_at: datetime | None
    first_overlapping_0p20_fill_at: datetime | None
    mfe_1h_pct: float
    mfe_3h_pct: float
    mfe_6h_pct: float
    mfe_12h_pct: float
    mfe_24h_pct: float
    mfe_48h_pct: float
    mfe_72h_pct: float
    mae_1h_pct: float
    mae_3h_pct: float
    mae_6h_pct: float
    mae_12h_pct: float
    mae_24h_pct: float
    mae_48h_pct: float
    mae_72h_pct: float
    alive_at_1h: bool
    alive_at_3h: bool
    alive_at_6h: bool
    alive_at_12h: bool
    alive_at_24h: bool
    alive_at_48h: bool
    alive_at_72h: bool


@dataclass(frozen=True, slots=True)
class MilestoneEvent:
    symbol: str
    direction: Direction
    touch_at: datetime
    fill_at: datetime
    milestone_pct: float
    reached: bool
    first_hit_minute: datetime | None
    hours_to_first_hit: float | None
    returned_to_entry_after: bool | None
    first_return_to_entry_at: datetime | None
    hard_stop_after: bool | None
    final_mfe_pct: float


@dataclass(frozen=True, slots=True)
class ActivationAnatomy:
    symbol: str
    direction: Direction
    touch_at: datetime
    fill_at: datetime
    activation_pct: float
    activation_at_minute: datetime
    max_mfe_after_activation_pct: float
    max_close_giveback_pct: float
    returned_to_entry_after_activation: bool
    first_return_to_entry_at: datetime | None
    later_new_high: bool
    hard_stop_after_activation: bool


@dataclass(frozen=True, slots=True)
class GivebackEvent:
    symbol: str
    direction: Direction
    touch_at: datetime
    fill_at: datetime
    activation_pct: float
    giveback_pct: float
    first_hit_at_close: datetime
    running_mfe_at_event_pct: float
    close_move_at_event_pct: float
    later_new_high: bool
    later_reached_plus_1p10: bool
    hard_stop_after_event: bool


@dataclass(frozen=True, slots=True)
class AdverseRecovery:
    symbol: str
    direction: Direction
    touch_at: datetime
    fill_at: datetime
    adverse_pct: float
    reached: bool
    first_hit_minute: datetime | None
    later_recovered_to_entry: bool | None
    later_reached_plus_0p50: bool | None
    later_reached_plus_1p10: bool | None
    final_mfe_pct: float


@dataclass(frozen=True, slots=True)
class OverlapEvent:
    active_symbol: str
    active_touch_at: datetime
    active_fill_at: datetime
    active_end_at: datetime
    next_touch_at: datetime
    next_direction: Direction
    next_filled_0p20: bool
    next_fill_at_0p20: datetime | None
    hours_after_active_fill: float


class Heartbeat:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(
        self,
        stage: str,
        processed: int,
        total: int,
        *,
        detail: str = "",
        force: bool = False,
    ) -> None:
        now = time.monotonic()
        if not force and now - self.last_emit < self.interval_seconds:
            return
        elapsed = max(0.0, now - self.started)
        eta = (
            None
            if processed <= 0 or processed >= total
            else elapsed / processed * (total - processed)
        )
        pct = 0.0 if total <= 0 else 100.0 * processed / total
        suffix = f" | {detail}" if detail else ""
        print(
            f"[EO3] stage={stage} processed={processed}/{total} ({pct:.1f}%) "
            f"elapsed={_duration(elapsed)} ETA={'n/a' if eta is None else _duration(eta)}{suffix}",
            flush=True,
        )
        self.last_emit = now


def _duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone: {value}")
    return parsed.astimezone(UTC)


def _as_float64(values: tuple[float, ...] | array[float]) -> NDArray[np.float64]:
    if isinstance(values, array) and values.typecode == "d":
        return np.frombuffer(values, dtype=np.float64)
    return np.asarray(values, dtype=np.float64)


def _directional_move(
    direction: Direction, entry: float, price: NDArray[np.float64]
) -> NDArray[np.float64]:
    raw = (price / entry - 1.0) * 100.0
    return raw if direction == "Long" else -raw


def _directional_scalar(direction: Direction, entry: float, price: float) -> float:
    raw = (price / entry - 1.0) * 100.0
    return raw if direction == "Long" else -raw


def _stop_price(fill: SourceFill) -> float:
    if fill.direction == "Long":
        return fill.fill_price * (1.0 - INITIAL_STOP_PCT / 100.0)
    return fill.fill_price * (1.0 + INITIAL_STOP_PCT / 100.0)


def _aggregate_minute_day(tape: TradeDay) -> MinuteDay:
    timestamps = _as_float64(tape.timestamps)
    prices = _as_float64(tape.prices)
    if timestamps.size == 0:
        raise ValueError("raw trade day is empty")
    minute_id = np.floor(timestamps / 60.0).astype(np.int64)
    changes = np.flatnonzero(np.diff(minute_id)) + 1
    starts = np.concatenate((np.asarray([0], dtype=np.int64), changes.astype(np.int64)))
    ends = np.concatenate((changes.astype(np.int64), np.asarray([prices.size], dtype=np.int64)))
    minute_ts = minute_id[starts].astype(np.float64) * 60.0
    high = np.maximum.reduceat(prices, starts).astype(np.float64, copy=False)
    low = np.minimum.reduceat(prices, starts).astype(np.float64, copy=False)
    close = prices[ends - 1].astype(np.float64, copy=False)
    if np.any(np.diff(minute_ts) <= 0):
        raise ValueError("minute aggregation is not strictly increasing")
    return MinuteDay(minute_ts, high, low, close)


def _cache_file(cache_root: Path, symbol: str, day: str) -> Path:
    return cache_root / symbol / f"{day}.npz"


def _load_cached_minute_day(path: Path, archive: Path, symbol: str) -> MinuteDay | None:
    if not path.exists():
        return None
    stat = archive.stat()
    try:
        with np.load(path, allow_pickle=False) as data:
            meta = json.loads(str(data["meta"].item()))
            expected = {
                "cache_version": CACHE_VERSION,
                "symbol": symbol,
                "archive_name": archive.name,
                "archive_size": stat.st_size,
                "archive_mtime_ns": stat.st_mtime_ns,
            }
            if any(meta.get(key) != value for key, value in expected.items()):
                return None
            minute_ts = np.asarray(data["minute_ts"], dtype=np.float64)
            high = np.asarray(data["high"], dtype=np.float64)
            low = np.asarray(data["low"], dtype=np.float64)
            close = np.asarray(data["close"], dtype=np.float64)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    if not (len(minute_ts) == len(high) == len(low) == len(close)) or len(minute_ts) == 0:
        return None
    return MinuteDay(minute_ts, high, low, close)


def _save_cached_minute_day(path: Path, archive: Path, symbol: str, day: MinuteDay) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stat = archive.stat()
    meta = {
        "cache_version": CACHE_VERSION,
        "symbol": symbol,
        "archive_name": archive.name,
        "archive_size": stat.st_size,
        "archive_mtime_ns": stat.st_mtime_ns,
    }
    temp = path.with_suffix(".tmp.npz")
    np.savez(
        temp,
        meta=np.asarray(json.dumps(meta, sort_keys=True)),
        minute_ts=day.minute_ts,
        high=day.high,
        low=day.low,
        close=day.close,
    )
    temp.replace(path)


def _trade_loader(symbol: str, heartbeat_seconds: float) -> Any:
    def load(path: Path) -> TradeDay:
        return _load_trade_day(
            path,
            progress_label=f"{symbol}/{path.name}",
            heartbeat_seconds=heartbeat_seconds,
            progress_sink=lambda text: print(text.replace("[P31 tape]", "[EO3 tape]"), flush=True),
        )

    return load


def _build_symbol_series(
    symbol: str,
    archive_by_day: dict[str, Path],
    cache_root: Path,
    raw_cache: TradeDayCache,
    heartbeat: Heartbeat,
) -> MinuteSeries:
    required = sorted(day for day in archive_by_day if day < FROZEN_END.date().isoformat())
    if not required:
        raise ValueError(f"EO3 no raw trade days for {symbol}")
    minute_parts: list[NDArray[np.float64]] = []
    high_parts: list[NDArray[np.float64]] = []
    low_parts: list[NDArray[np.float64]] = []
    close_parts: list[NDArray[np.float64]] = []
    for index, day_name in enumerate(required, start=1):
        archive = archive_by_day[day_name]
        cached = _load_cached_minute_day(_cache_file(cache_root, symbol, day_name), archive, symbol)
        if cached is None:
            cached = _aggregate_minute_day(raw_cache.get(archive))
            _save_cached_minute_day(
                _cache_file(cache_root, symbol, day_name), archive, symbol, cached
            )
            status = "built"
        else:
            status = "cached"
        minute_parts.append(cached.minute_ts)
        high_parts.append(cached.high)
        low_parts.append(cached.low)
        close_parts.append(cached.close)
        heartbeat.emit("minute_cache", index, len(required), detail=f"{symbol} {day_name} {status}")
    series = MinuteSeries(
        np.concatenate(minute_parts),
        np.concatenate(high_parts),
        np.concatenate(low_parts),
        np.concatenate(close_parts),
    )
    if np.any(np.diff(series.minute_ts) <= 0):
        raise ValueError(f"EO3 combined minute series is not strictly increasing: {symbol}")
    return series


def _raw_prices_between(
    tape: TradeDay, start_ts: float, end_ts: float
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    timestamps = _as_float64(tape.timestamps)
    prices = _as_float64(tape.prices)
    left = int(np.searchsorted(timestamps, start_ts, side="left"))
    right = int(np.searchsorted(timestamps, end_ts, side="left"))
    return timestamps[left:right], prices[left:right]


def _first_stop_raw(
    fill: SourceFill,
    tape: TradeDay,
    start_ts: float,
    end_ts: float,
) -> tuple[float, float, float, float] | None:
    timestamps, prices = _raw_prices_between(tape, start_ts, end_ts)
    if prices.size == 0:
        return None
    stop = _stop_price(fill)
    mask = prices <= stop if fill.direction == "Long" else prices >= stop
    hits = np.flatnonzero(mask)
    if hits.size == 0:
        return None
    index = int(hits[0])
    prefix = prices[: index + 1]
    moves = _directional_move(fill.direction, fill.fill_price, prefix)
    return (
        float(timestamps[index]),
        float(prices[index]),
        float(np.max(moves)),
        float(np.min(moves)),
    )


def _minute_slice_for_fill(
    fill: SourceFill,
    series: MinuteSeries,
    archive_by_day: dict[str, Path],
    raw_cache: TradeDayCache,
) -> tuple[
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    NDArray[np.float64],
    datetime | None,
]:
    fill_ts = fill.fill_at.timestamp()
    fill_minute = math.floor(fill_ts / 60.0) * 60.0
    frozen_ts = FROZEN_END.timestamp()
    start = int(np.searchsorted(series.minute_ts, fill_minute, side="left"))
    end = int(np.searchsorted(series.minute_ts, frozen_ts, side="left"))
    minute_ts = series.minute_ts[start:end].copy()
    high = series.high[start:end].copy()
    low = series.low[start:end].copy()
    close = series.close[start:end].copy()
    if minute_ts.size == 0:
        raise ValueError(f"EO3 no minute path after fill: {fill.symbol} {fill.fill_at}")

    first_day = fill.fill_at.date().isoformat()
    if first_day not in archive_by_day:
        raise FileNotFoundError(f"EO3 fill-day archive missing: {fill.symbol} {first_day}")
    first_tape = raw_cache.get(archive_by_day[first_day])
    first_end = min(fill_minute + 60.0, frozen_ts)
    first_raw_ts, first_raw_price = _raw_prices_between(first_tape, fill_ts, first_end)
    if first_raw_price.size == 0:
        raise ValueError(
            f"EO3 no raw ticks from fill through fill minute: {fill.symbol} {fill.fill_at}"
        )
    if minute_ts[0] == fill_minute:
        high[0] = float(np.max(first_raw_price))
        low[0] = float(np.min(first_raw_price))
        close[0] = float(first_raw_price[-1])
    else:
        minute_ts = np.concatenate((np.asarray([fill_minute], dtype=np.float64), minute_ts))
        high = np.concatenate((np.asarray([np.max(first_raw_price)], dtype=np.float64), high))
        low = np.concatenate((np.asarray([np.min(first_raw_price)], dtype=np.float64), low))
        close = np.concatenate((np.asarray([first_raw_price[-1]], dtype=np.float64), close))

    adverse_source = low if fill.direction == "Long" else high
    adverse = _directional_move(fill.direction, fill.fill_price, adverse_source)
    stop_candidates = np.flatnonzero(adverse <= -INITIAL_STOP_PCT)
    stop_at: datetime | None = None
    if stop_candidates.size:
        stop_index = int(stop_candidates[0])
        stop_minute = float(minute_ts[stop_index])
        stop_day = datetime.fromtimestamp(stop_minute, UTC).date().isoformat()
        if stop_day not in archive_by_day:
            raise FileNotFoundError(f"EO3 stop-day archive missing: {fill.symbol} {stop_day}")
        stop_tape = raw_cache.get(archive_by_day[stop_day])
        raw_start = max(fill_ts, stop_minute)
        raw_end = min(stop_minute + 60.0, frozen_ts)
        exact = _first_stop_raw(fill, stop_tape, raw_start, raw_end)
        if exact is None:
            raise ValueError(f"EO3 1m/raw stop equivalence failed: {fill.symbol} {fill.fill_at}")
        exact_ts, exact_price, exact_mfe, exact_mae = exact
        stop_at = datetime.fromtimestamp(exact_ts, UTC)
        minute_ts = minute_ts[: stop_index + 1]
        high = high[: stop_index + 1]
        low = low[: stop_index + 1]
        close = close[: stop_index + 1]
        if fill.direction == "Long":
            high[stop_index] = fill.fill_price * (1.0 + exact_mfe / 100.0)
            low[stop_index] = fill.fill_price * (1.0 + exact_mae / 100.0)
        else:
            low[stop_index] = fill.fill_price * (1.0 - exact_mfe / 100.0)
            high[stop_index] = fill.fill_price * (1.0 - exact_mae / 100.0)
        close[stop_index] = exact_price
    return minute_ts, high, low, close, stop_at


def _first_true_index(mask: NDArray[np.bool_]) -> int | None:
    hits = np.flatnonzero(mask)
    return None if hits.size == 0 else int(hits[0])


def _close_times(minute_ts: NDArray[np.float64]) -> NDArray[np.float64]:
    return minute_ts + 60.0


def _first_return_after(close_move: NDArray[np.float64], start_index: int) -> int | None:
    if start_index + 1 >= close_move.size:
        return None
    relative = _first_true_index(close_move[start_index + 1 :] <= 0.0)
    return None if relative is None else start_index + 1 + relative


def _milestone_key(level: float) -> str:
    return f"{level:.2f}".replace(".", "p")


def _horizon_extreme(
    minute_ts: NDArray[np.float64],
    favorable: NDArray[np.float64],
    adverse: NDArray[np.float64],
    fill_at: datetime,
    end_at: datetime,
    hours: int,
) -> tuple[float, float, bool]:
    horizon_ts = (fill_at + timedelta(hours=hours)).timestamp()
    limit = min(horizon_ts, end_at.timestamp())
    count = int(np.searchsorted(minute_ts, limit, side="left"))
    if count <= 0:
        return 0.0, 0.0, end_at >= fill_at + timedelta(hours=hours)
    return (
        max(0.0, float(np.max(favorable[:count]))),
        min(0.0, float(np.min(adverse[:count]))),
        end_at >= fill_at + timedelta(hours=hours),
    )


def _post_stop_metrics(
    fill: SourceFill,
    stop_at: datetime | None,
    series: MinuteSeries,
) -> tuple[float | None, bool | None, bool | None, bool | None]:
    if stop_at is None:
        return None, None, None, None
    start_ts = stop_at.timestamp()
    end_ts = min((stop_at + timedelta(hours=POST_STOP_HOURS)).timestamp(), FROZEN_END.timestamp())
    first_full_minute = math.floor(start_ts / 60.0) * 60.0 + 60.0
    left = int(np.searchsorted(series.minute_ts, first_full_minute, side="left"))
    right = int(np.searchsorted(series.minute_ts, end_ts, side="left"))
    if right <= left:
        return 0.0, False, False, False
    high = series.high[left:right]
    low = series.low[left:right]
    favorable_source = high if fill.direction == "Long" else low
    favorable = _directional_move(fill.direction, fill.fill_price, favorable_source)
    raw_mfe = float(np.max(favorable))
    mfe = max(0.0, raw_mfe)
    return mfe, raw_mfe >= 0.0, raw_mfe >= 0.50, raw_mfe >= 1.10


def _signal_rows(
    source_report_dir: Path, fills: tuple[SourceFill, ...]
) -> tuple[SourceSignal, ...]:
    events = source_report_dir / "entry_offset_adverse_events.csv"
    if _sha256(events) != SOURCE_EVENT_SHA256:
        raise ValueError("EO3 source event-table hash mismatch")
    filled_by_key = {(row.symbol, row.touch_at.isoformat()): row for row in fills}
    unique: dict[tuple[str, str], SourceSignal] = {}
    with events.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row["symbol"])
            touch = _parse_dt(str(row["touch_at"]))
            key = (symbol, touch.isoformat())
            if key in unique:
                continue
            direction_raw = str(row["direction"])
            if direction_raw not in {"Long", "Short"}:
                raise ValueError(f"EO3 unknown direction: {direction_raw}")
            fill = filled_by_key.get(key)
            unique[key] = SourceSignal(
                symbol=symbol,
                direction=cast(Direction, direction_raw),
                touch_at=touch,
                filled_0p20=fill is not None,
                fill_at_0p20=None if fill is None else fill.fill_at,
            )
    if len(unique) != 1063:
        raise ValueError(f"EO3 expected 1063 unique source signals, got {len(unique)}")
    return tuple(sorted(unique.values(), key=lambda row: (row.symbol, row.touch_at)))


def _overlaps_for_trade(
    fill: SourceFill,
    end_at: datetime,
    symbol_signals: tuple[SourceSignal, ...],
) -> tuple[list[OverlapEvent], int, int, datetime | None, datetime | None]:
    events: list[OverlapEvent] = []
    first_signal: datetime | None = None
    first_fill: datetime | None = None
    fill_count = 0
    for signal in symbol_signals:
        if signal.touch_at <= fill.touch_at or signal.touch_at >= end_at:
            continue
        if first_signal is None:
            first_signal = signal.touch_at
        if signal.filled_0p20:
            fill_count += 1
            if signal.fill_at_0p20 is not None and first_fill is None:
                first_fill = signal.fill_at_0p20
        events.append(
            OverlapEvent(
                active_symbol=fill.symbol,
                active_touch_at=fill.touch_at,
                active_fill_at=fill.fill_at,
                active_end_at=end_at,
                next_touch_at=signal.touch_at,
                next_direction=signal.direction,
                next_filled_0p20=signal.filled_0p20,
                next_fill_at_0p20=signal.fill_at_0p20,
                hours_after_active_fill=(signal.touch_at - fill.fill_at).total_seconds() / 3600.0,
            )
        )
    return events, len(events), fill_count, first_signal, first_fill


def analyse_trade(
    fill: SourceFill,
    series: MinuteSeries,
    archive_by_day: dict[str, Path],
    raw_cache: TradeDayCache,
    symbol_signals: tuple[SourceSignal, ...],
) -> tuple[
    TradeAnatomy,
    list[MilestoneEvent],
    list[ActivationAnatomy],
    list[GivebackEvent],
    list[AdverseRecovery],
    list[OverlapEvent],
]:
    minute_ts, high, low, close, stop_at = _minute_slice_for_fill(
        fill, series, archive_by_day, raw_cache
    )
    favorable_source = high if fill.direction == "Long" else low
    favorable = _directional_move(fill.direction, fill.fill_price, favorable_source)
    adverse_source = low if fill.direction == "Long" else high
    adverse = _directional_move(fill.direction, fill.fill_price, adverse_source)
    close_move = _directional_move(fill.direction, fill.fill_price, close)
    favorable = np.maximum(favorable, 0.0)
    adverse = np.minimum(adverse, 0.0)
    running_mfe = np.maximum.accumulate(favorable)
    giveback_close = np.maximum(0.0, running_mfe - close_move)
    suffix_mfe = np.maximum.accumulate(favorable[::-1])[::-1]
    close_times = _close_times(minute_ts)
    if stop_at is not None:
        close_times[-1] = stop_at.timestamp()

    end_at = stop_at if stop_at is not None else FROZEN_END
    end_reason: EndReason = "initial_stop" if stop_at is not None else "data_end_open"
    mfe_index = int(np.argmax(favorable))
    mfe = float(favorable[mfe_index])
    mae = float(np.min(adverse))

    milestone_events: list[MilestoneEvent] = []
    milestone_indices: dict[float, int] = {}
    for level in MILESTONES_PCT:
        hit = _first_true_index(favorable >= level)
        if hit is not None:
            milestone_indices[level] = hit
            returned_index = _first_return_after(close_move, hit)
            milestone_events.append(
                MilestoneEvent(
                    symbol=fill.symbol,
                    direction=fill.direction,
                    touch_at=fill.touch_at,
                    fill_at=fill.fill_at,
                    milestone_pct=level,
                    reached=True,
                    first_hit_minute=datetime.fromtimestamp(float(minute_ts[hit]), UTC),
                    hours_to_first_hit=max(
                        0.0,
                        (float(minute_ts[hit]) - fill.fill_at.timestamp()) / 3600.0,
                    ),
                    returned_to_entry_after=returned_index is not None,
                    first_return_to_entry_at=(
                        None
                        if returned_index is None
                        else datetime.fromtimestamp(float(close_times[returned_index]), UTC)
                    ),
                    hard_stop_after=stop_at is not None,
                    final_mfe_pct=mfe,
                )
            )
        else:
            milestone_events.append(
                MilestoneEvent(
                    symbol=fill.symbol,
                    direction=fill.direction,
                    touch_at=fill.touch_at,
                    fill_at=fill.fill_at,
                    milestone_pct=level,
                    reached=False,
                    first_hit_minute=None,
                    hours_to_first_hit=None,
                    returned_to_entry_after=None,
                    first_return_to_entry_at=None,
                    hard_stop_after=None,
                    final_mfe_pct=mfe,
                )
            )

    activation_rows: list[ActivationAnatomy] = []
    giveback_rows: list[GivebackEvent] = []
    for activation in ACTIVATION_LEVELS_PCT:
        hit = milestone_indices.get(activation)
        if hit is None:
            continue
        after_running = running_mfe[hit:]
        after_close = close_move[hit:]
        after_giveback = np.maximum(0.0, after_running - after_close)
        return_relative = (
            _first_true_index(after_close[1:] <= 0.0)
            if after_close.size > 1
            else None
        )
        return_index = None if return_relative is None else hit + 1 + return_relative
        later_new_high = bool(
            suffix_mfe[min(hit + 1, len(suffix_mfe) - 1)]
            > running_mfe[hit] + 1e-12
        )
        activation_rows.append(
            ActivationAnatomy(
                symbol=fill.symbol,
                direction=fill.direction,
                touch_at=fill.touch_at,
                fill_at=fill.fill_at,
                activation_pct=activation,
                activation_at_minute=datetime.fromtimestamp(float(minute_ts[hit]), UTC),
                max_mfe_after_activation_pct=float(np.max(favorable[hit:])),
                max_close_giveback_pct=float(np.max(after_giveback)),
                returned_to_entry_after_activation=return_index is not None,
                first_return_to_entry_at=(
                    None
                    if return_index is None
                    else datetime.fromtimestamp(float(close_times[return_index]), UTC)
                ),
                later_new_high=later_new_high,
                hard_stop_after_activation=stop_at is not None,
            )
        )
        for giveback in GIVEBACK_LEVELS_PCT:
            relative = _first_true_index(after_giveback >= giveback)
            if relative is None:
                continue
            index = hit + relative
            peak_at_event = float(running_mfe[index])
            later_high = (
                float(suffix_mfe[min(index + 1, len(suffix_mfe) - 1)])
                if index + 1 < len(suffix_mfe)
                else peak_at_event
            )
            giveback_rows.append(
                GivebackEvent(
                    symbol=fill.symbol,
                    direction=fill.direction,
                    touch_at=fill.touch_at,
                    fill_at=fill.fill_at,
                    activation_pct=activation,
                    giveback_pct=giveback,
                    first_hit_at_close=datetime.fromtimestamp(float(close_times[index]), UTC),
                    running_mfe_at_event_pct=peak_at_event,
                    close_move_at_event_pct=float(close_move[index]),
                    later_new_high=later_high > peak_at_event + 1e-12,
                    later_reached_plus_1p10=later_high >= 1.10,
                    hard_stop_after_event=stop_at is not None,
                )
            )

    adverse_rows: list[AdverseRecovery] = []
    for level in ADVERSE_LEVELS_PCT:
        hit = _first_true_index(adverse <= -level)
        if hit is None:
            adverse_rows.append(
                AdverseRecovery(
                    fill.symbol,
                    fill.direction,
                    fill.touch_at,
                    fill.fill_at,
                    level,
                    False,
                    None,
                    None,
                    None,
                    None,
                    mfe,
                )
            )
            continue
        later_mfe = float(suffix_mfe[hit])
        adverse_rows.append(
            AdverseRecovery(
                fill.symbol,
                fill.direction,
                fill.touch_at,
                fill.fill_at,
                level,
                True,
                datetime.fromtimestamp(float(minute_ts[hit]), UTC),
                later_mfe >= 0.0,
                later_mfe >= 0.50,
                later_mfe >= 1.10,
                mfe,
            )
        )

    (
        overlap_rows,
        overlap_count,
        overlap_fill_count,
        first_overlap,
        first_overlap_fill,
    ) = _overlaps_for_trade(fill, end_at, symbol_signals)
    post_stop = _post_stop_metrics(fill, stop_at, series)
    horizon_values: dict[int, tuple[float, float, bool]] = {
        hours: _horizon_extreme(minute_ts, favorable, adverse, fill.fill_at, end_at, hours)
        for hours in HORIZONS_HOURS
    }

    def returned(level: float) -> bool:
        hit = milestone_indices.get(level)
        return False if hit is None else _first_return_after(close_move, hit) is not None

    anatomy = TradeAnatomy(
        symbol=fill.symbol,
        direction=fill.direction,
        touch_at=fill.touch_at,
        fill_at=fill.fill_at,
        fill_price=fill.fill_price,
        end_reason=end_reason,
        end_at=end_at,
        duration_hours=(end_at - fill.fill_at).total_seconds() / 3600.0,
        mfe_pct=mfe,
        mae_pct=mae,
        mfe_at_minute=datetime.fromtimestamp(float(minute_ts[mfe_index]), UTC),
        max_milestone_pct=max(
            (level for level in MILESTONES_PCT if level in milestone_indices),
            default=0.0,
        ),
        max_close_giveback_from_running_mfe_pct=float(np.max(giveback_close)),
        returned_to_entry_after_plus_0p30=returned(0.30),
        returned_to_entry_after_plus_0p50=returned(0.50),
        returned_to_entry_after_plus_0p75=returned(0.75),
        returned_to_entry_after_plus_1p00=returned(1.00),
        returned_to_entry_after_plus_1p10=returned(1.10),
        hard_stop_after_plus_0p50=0.50 in milestone_indices and stop_at is not None,
        hard_stop_after_plus_0p75=0.75 in milestone_indices and stop_at is not None,
        hard_stop_after_plus_1p00=1.00 in milestone_indices and stop_at is not None,
        hard_stop_after_plus_1p10=1.10 in milestone_indices and stop_at is not None,
        post_stop_72h_mfe_pct=post_stop[0],
        post_stop_72h_returned_to_entry=post_stop[1],
        post_stop_72h_reached_plus_0p50=post_stop[2],
        post_stop_72h_reached_plus_1p10=post_stop[3],
        overlapping_signals=overlap_count,
        overlapping_0p20_fills=overlap_fill_count,
        first_overlapping_signal_at=first_overlap,
        first_overlapping_0p20_fill_at=first_overlap_fill,
        mfe_1h_pct=horizon_values[1][0],
        mfe_3h_pct=horizon_values[3][0],
        mfe_6h_pct=horizon_values[6][0],
        mfe_12h_pct=horizon_values[12][0],
        mfe_24h_pct=horizon_values[24][0],
        mfe_48h_pct=horizon_values[48][0],
        mfe_72h_pct=horizon_values[72][0],
        mae_1h_pct=horizon_values[1][1],
        mae_3h_pct=horizon_values[3][1],
        mae_6h_pct=horizon_values[6][1],
        mae_12h_pct=horizon_values[12][1],
        mae_24h_pct=horizon_values[24][1],
        mae_48h_pct=horizon_values[48][1],
        mae_72h_pct=horizon_values[72][1],
        alive_at_1h=horizon_values[1][2],
        alive_at_3h=horizon_values[3][2],
        alive_at_6h=horizon_values[6][2],
        alive_at_12h=horizon_values[12][2],
        alive_at_24h=horizon_values[24][2],
        alive_at_48h=horizon_values[48][2],
        alive_at_72h=horizon_values[72][2],
    )
    return anatomy, milestone_events, activation_rows, giveback_rows, adverse_rows, overlap_rows


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _rows_from_dataclasses(rows: list[Any]) -> list[dict[str, Any]]:
    return [{key: _json_value(value) for key, value in asdict(row).items()} for row in rows]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def summarize(anatomy: list[TradeAnatomy], milestones: list[MilestoneEvent]) -> dict[str, Any]:
    stops = [row for row in anatomy if row.end_reason == "initial_stop"]
    opens = [row for row in anatomy if row.end_reason == "data_end_open"]
    milestone_summary: dict[str, Any] = {}
    for level in MILESTONES_PCT:
        subset = [row for row in milestones if row.milestone_pct == level]
        reached = [row for row in subset if row.reached]
        returned = [row for row in reached if row.returned_to_entry_after]
        stopped = [row for row in reached if row.hard_stop_after]
        milestone_summary[_milestone_key(level)] = {
            "level_pct": level,
            "reached": len(reached),
            "reached_pct": 100.0 * len(reached) / len(anatomy),
            "returned_to_entry_after": len(returned),
            "returned_to_entry_after_pct_of_reached": (
                100.0 * len(returned) / len(reached) if reached else None
            ),
            "hard_stop_after": len(stopped),
            "hard_stop_after_pct_of_reached": (
                100.0 * len(stopped) / len(reached) if reached else None
            ),
        }
    mfe_values = [row.mfe_pct for row in anatomy]
    giveback_values = [row.max_close_giveback_from_running_mfe_pct for row in anatomy]
    return {
        "research": RESEARCH_VERSION,
        "engine_revision": ENGINE_REVISION,
        "filled_trades": len(anatomy),
        "hard_stop_minus_1p00": len(stops),
        "data_end_open_without_profit_exit": len(opens),
        "mfe_median_pct": _quantile(mfe_values, 0.50),
        "mfe_p75_pct": _quantile(mfe_values, 0.75),
        "mfe_p90_pct": _quantile(mfe_values, 0.90),
        "mfe_p95_pct": _quantile(mfe_values, 0.95),
        "mfe_max_pct": max(mfe_values) if mfe_values else None,
        "max_close_giveback_median_pct": _quantile(giveback_values, 0.50),
        "max_close_giveback_p90_pct": _quantile(giveback_values, 0.90),
        "trades_with_overlapping_signal": sum(row.overlapping_signals > 0 for row in anatomy),
        "trades_with_overlapping_0p20_fill": sum(row.overlapping_0p20_fills > 0 for row in anatomy),
        "total_overlapping_signals": sum(row.overlapping_signals for row in anatomy),
        "total_overlapping_0p20_fills": sum(row.overlapping_0p20_fills for row in anatomy),
        "milestones": milestone_summary,
        "initial_stop_pct": INITIAL_STOP_PCT,
        "profit_target": "DISABLED_FOR_ANATOMY",
        "positive_floor": "DISABLED",
        "frozen_end": FROZEN_END.isoformat(),
        "post_stop_research_horizon_hours": POST_STOP_HOURS,
        "downloads": "DISABLED",
        "production_effect": "NONE",
    }


def _per_symbol(anatomy: list[TradeAnatomy]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol in ALL_SYMBOLS:
        subset = [row for row in anatomy if row.symbol == symbol]
        rows.append(
            {
                "symbol": symbol,
                "fills": len(subset),
                "hard_stop": sum(row.end_reason == "initial_stop" for row in subset),
                "data_end_open": sum(row.end_reason == "data_end_open" for row in subset),
                "mfe_median_pct": _quantile([row.mfe_pct for row in subset], 0.50),
                "mfe_p90_pct": _quantile([row.mfe_pct for row in subset], 0.90),
                "reached_plus_1p10": sum(row.max_milestone_pct >= 1.10 for row in subset),
                "reached_plus_3p00": sum(row.max_milestone_pct >= 3.00 for row in subset),
                "reached_plus_5p00": sum(row.max_milestone_pct >= 5.00 for row in subset),
                "reached_plus_10p00": sum(row.max_milestone_pct >= 10.00 for row in subset),
                "with_overlapping_signal": sum(row.overlapping_signals > 0 for row in subset),
            }
        )
    return rows


def _summary_md(summary: dict[str, Any]) -> str:
    lines = [
        "# EO3 Full Path / MFE-Giveback-Retest Anatomy",
        "",
        "Research-only anatomy of all 846 exact EO1 ADVERSE_0P20 fills.",
        "There is no profit target and no +0.10 floor in this anatomy pass.",
        "The trade-life boundary is only the unchanged -1.00% hard stop or frozen-data end.",
        "Milestones are observations, not exits. Giveback is evaluated at causal 1m closes.",
        "Fill/stop boundary minutes are resolved against raw public-trade ticks.",
        "",
        "## ALL9",
        "",
        f"- Fills: **{summary['filled_trades']}**",
        f"- Eventually hit -1.00 hard stop: **{summary['hard_stop_minus_1p00']}**",
        "- Still alive at frozen-data end without profit exit: "
        f"**{summary['data_end_open_without_profit_exit']}**",
        f"- Median MFE: **{float(summary['mfe_median_pct'] or 0.0):.3f}%**",
        f"- P90 MFE: **{float(summary['mfe_p90_pct'] or 0.0):.3f}%**",
        f"- Max MFE: **{float(summary['mfe_max_pct'] or 0.0):.3f}%**",
        "- Trades overlapping at least one later source signal: "
        f"**{summary['trades_with_overlapping_signal']}**",
        "- Trades overlapping at least one later -0.20 fill: "
        f"**{summary['trades_with_overlapping_0p20_fill']}**",
        "",
        "## Milestones",
        "",
        "| Level | Reached | % fills | Returned to Entry after | Hard stop after |",
        "|---:|---:|---:|---:|---:|",
    ]
    for level in MILESTONES_PCT:
        item = summary["milestones"][_milestone_key(level)]
        lines.append(
            f"| +{level:.2f}% | {item['reached']} | {float(item['reached_pct']):.2f}% | "
            f"{item['returned_to_entry_after']} | {item['hard_stop_after']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- EO3 does not choose, optimize or recommend an Exit rule.",
            "- +1.10 is only a milestone here; winners are not truncated at +1.10.",
            "- 1m close giveback is causal and avoids inventing intraminute OHLC order.",
            "- Post-stop 72h fields are research-only continuation and never live "
            "inputs at the stop time.",
            "- Overlap is descriptive. This is not yet a finite-capital portfolio backtest.",
            "- Downloads are disabled; missing/corrupt raw days fail closed.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(
    project_root: Path,
    source_report_dir: Path,
    output_dir: Path,
    cache_root: Path,
    *,
    raw_day_cache_size: int,
    heartbeat_seconds: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    fills = load_source_fills(source_report_dir)
    if len(fills) != EXPECTED_FILLED_0P20:
        raise ValueError(f"EO3 cohort mismatch: {len(fills)} != {EXPECTED_FILLED_0P20}")
    signals = _signal_rows(source_report_dir, fills)
    sources = discover_sources(project_root)
    source_by_symbol = {source.symbol: source for source in sources}
    heartbeat = Heartbeat(heartbeat_seconds)

    anatomy_rows: list[TradeAnatomy] = []
    milestone_rows: list[MilestoneEvent] = []
    activation_rows: list[ActivationAnatomy] = []
    giveback_rows: list[GivebackEvent] = []
    adverse_rows: list[AdverseRecovery] = []
    overlap_rows: list[OverlapEvent] = []

    total = len(fills)
    processed = 0
    for symbol in ALL_SYMBOLS:
        symbol_fills = [fill for fill in fills if fill.symbol == symbol]
        if not symbol_fills:
            continue
        if symbol not in source_by_symbol:
            raise ValueError(f"EO3 missing dataset source: {symbol}")
        archive_by_day = _archive_map(source_by_symbol[symbol].dataset_dir)
        raw_cache = TradeDayCache(
            max_days=raw_day_cache_size,
            loader=_trade_loader(symbol, heartbeat_seconds),
        )
        series = _build_symbol_series(symbol, archive_by_day, cache_root, raw_cache, heartbeat)
        symbol_signals = tuple(row for row in signals if row.symbol == symbol)
        for fill in symbol_fills:
            result = analyse_trade(fill, series, archive_by_day, raw_cache, symbol_signals)
            anatomy_rows.append(result[0])
            milestone_rows.extend(result[1])
            activation_rows.extend(result[2])
            giveback_rows.extend(result[3])
            adverse_rows.extend(result[4])
            overlap_rows.extend(result[5])
            processed += 1
            heartbeat.emit(
                "trade_anatomy",
                processed,
                total,
                detail=f"{symbol} {fill.fill_at.isoformat()}",
            )
        heartbeat.emit("symbol_complete", processed, total, detail=symbol, force=True)

    if len(anatomy_rows) != EXPECTED_FILLED_0P20:
        raise ValueError(
            f"EO3 result count mismatch: {len(anatomy_rows)} != {EXPECTED_FILLED_0P20}"
        )
    anatomy_rows.sort(key=lambda row: (row.symbol, row.fill_at, row.touch_at))
    summary = summarize(anatomy_rows, milestone_rows)
    per_symbol = _per_symbol(anatomy_rows)

    outputs: dict[str, list[dict[str, Any]]] = {
        "eo3_trade_anatomy.csv": _rows_from_dataclasses(anatomy_rows),
        "eo3_milestone_events.csv": _rows_from_dataclasses(milestone_rows),
        "eo3_activation_anatomy.csv": _rows_from_dataclasses(activation_rows),
        "eo3_giveback_events.csv": _rows_from_dataclasses(giveback_rows),
        "eo3_adverse_recovery.csv": _rows_from_dataclasses(adverse_rows),
        "eo3_overlap_events.csv": _rows_from_dataclasses(overlap_rows),
        "per_symbol.csv": per_symbol,
    }
    for name, rows in outputs.items():
        _write_csv(output_dir / name, rows)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "SUMMARY_RU.md").write_text(_summary_md(summary), encoding="utf-8")

    provenance = {
        **summary,
        "completed_at": datetime.now(UTC).isoformat(),
        "period_tag": PERIOD_TAG,
        "source_report_dir": str(source_report_dir),
        "source_event_sha256": SOURCE_EVENT_SHA256,
        "cache_version": CACHE_VERSION,
        "cache_root": str(cache_root),
        "minute_anatomy": (
            "1m OHLC extremes + causal 1m closes; raw tick resolution for fill "
            "and hard-stop boundary minutes"
        ),
        "machine_truth_sha256": {name: _sha256(output_dir / name) for name in outputs},
        "summary_sha256": _sha256(summary_path),
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    heartbeat.emit("complete", total, total, detail=str(output_dir), force=True)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EO3 full path anatomy for all 846 -0.20 fills")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source-report-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--raw-day-cache-size", type=int, default=3)
    parser.add_argument("--heartbeat-seconds", type=float, default=25.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if int(args.raw_day_cache_size) <= 0:
        raise ValueError("raw-day-cache-size must be positive")
    if float(args.heartbeat_seconds) <= 0:
        raise ValueError("heartbeat-seconds must be positive")
    run(
        args.project_root.resolve(),
        args.source_report_dir.resolve(),
        args.output_dir.resolve(),
        args.cache_root.resolve(),
        raw_day_cache_size=int(args.raw_day_cache_size),
        heartbeat_seconds=float(args.heartbeat_seconds),
    )


if __name__ == "__main__":
    main()
