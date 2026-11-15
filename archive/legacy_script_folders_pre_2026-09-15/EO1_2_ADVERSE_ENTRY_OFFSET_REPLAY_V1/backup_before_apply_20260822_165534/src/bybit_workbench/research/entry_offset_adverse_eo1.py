from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    PathSeries,
    SignalSource,
    TradeDayCache,
    build_path_series,
    directional_move_pct,
    discover_source,
    load_core_signals,
)
from bybit_workbench.research.flow_reversal_v1 import TradeDay, _archive_map, _load_trade_day
from bybit_workbench.research.mtf_entry import Direction

RESEARCH_VERSION = "EO1_ADVERSE_ENTRY_OFFSET_REPLAY_V1"
PERIOD_TAG = "20260518_20260816"
ALL_SYMBOLS = (
    "UNIUSDT",
    "LINKUSDT",
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "1000PEPEUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "ADAUSDT",
)
DEV_SYMBOLS = ("UNIUSDT", "LINKUSDT")
HOLDOUT_SYMBOLS = tuple(symbol for symbol in ALL_SYMBOLS if symbol not in DEV_SYMBOLS)
EXPECTED_COUNTS = {
    "UNIUSDT": 113,
    "LINKUSDT": 114,
    "BTCUSDT": 119,
    "ETHUSDT": 130,
    "XRPUSDT": 125,
    "1000PEPEUSDT": 117,
    "SOLUSDT": 91,
    "DOGEUSDT": 143,
    "ADAUSDT": 111,
}
EXPECTED_SIGNAL_COUNT = 1063
ENTRY_ADVERSE_OFFSETS_PCT = (0.00, 0.10, 0.20)
TEMPORAL_FOLDS = (
    ("F1", datetime(2026, 5, 18, tzinfo=UTC), datetime(2026, 6, 17, tzinfo=UTC)),
    ("F2", datetime(2026, 6, 17, tzinfo=UTC), datetime(2026, 7, 17, tzinfo=UTC)),
    ("F3", datetime(2026, 7, 17, tzinfo=UTC), datetime(2026, 8, 17, tzinfo=UTC)),
)

FillStatus = Literal[
    "filled",
    "original_target_before_fill",
    "pending_horizon_no_fill",
    "data_end_no_fill",
]
ExitReason = Literal["target", "initial_stop", "positive_floor", "horizon", "data_end"]


@dataclass(frozen=True, slots=True)
class Config:
    target_pct: float = 1.10
    initial_stop_pct: float = 1.00
    activation_pct: float = 0.10
    positive_floor_pct: float = 0.10
    pending_horizon_hours: int = 72
    trade_horizon_hours: int = 72
    day_cache_size: int = 10
    progress_interval_seconds: float = 20.0
    illustrative_round_trip_cost_pct: float = 0.10
    margin_usd: float = 100.0
    leverage: float = 10.0

    def __post_init__(self) -> None:
        if self.target_pct <= 0 or self.initial_stop_pct <= 0:
            raise ValueError("target_pct and initial_stop_pct must be positive")
        if self.activation_pct < 0 or self.positive_floor_pct < 0:
            raise ValueError("activation/floor cannot be negative")
        if self.activation_pct < self.positive_floor_pct:
            raise ValueError("activation_pct must be >= positive_floor_pct")
        if self.pending_horizon_hours <= 0 or self.trade_horizon_hours <= 0:
            raise ValueError("horizons must be positive")
        if self.day_cache_size <= 0 or self.progress_interval_seconds <= 0:
            raise ValueError("cache/progress settings must be positive")
        if self.illustrative_round_trip_cost_pct < 0:
            raise ValueError("illustrative cost cannot be negative")
        if self.margin_usd <= 0 or self.leverage <= 0:
            raise ValueError("margin/leverage must be positive")

    @property
    def max_path_hours(self) -> int:
        return self.pending_horizon_hours + self.trade_horizon_hours


@dataclass(frozen=True, slots=True)
class EventResult:
    symbol: str
    direction: Direction
    touch_at: datetime
    original_entry_price: float
    adverse_offset_pct: float
    scenario: str
    pending_entry_price: float
    fill_status: FillStatus
    fill_at: datetime | None
    fill_price_ideal: float | None
    first_cross_signal_move_pct: float | None
    first_cross_price: float | None
    trigger_slippage_bps_proxy: float | None
    seconds_touch_to_fill: float | None
    original_target_before_fill_at: datetime | None
    offset_touched_anytime_pending_72h: bool
    offset_touch_anytime_at: datetime | None
    protection_activated: bool
    protection_activation_at: datetime | None
    exit_reason: ExitReason | None
    exit_at: datetime | None
    exit_move_from_fill_pct_observed: float | None
    theoretical_exit_level_pct: float | None
    gross_pnl_pct_theoretical: float | None
    net_pnl_pct_after_cost_reserve: float | None
    pnl_usd_100_margin_10x: float | None
    mfe_from_fill_pct: float | None
    mae_from_fill_pct: float | None
    pending_window_complete: bool
    trade_window_complete: bool | None
    missing_archive_days: str


class Progress:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(
        self,
        processed: int,
        total: int,
        *,
        force: bool = False,
        detail: str = "",
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
        suffix = f" | {detail}" if detail else ""
        print(
            f"[EO1] processed={processed}/{total} "
            f"({100.0 * processed / max(1, total):.1f}%) "
            f"elapsed={_duration(elapsed)} "
            f"ETA={'n/a' if eta is None else _duration(eta)}{suffix}",
            flush=True,
        )
        self.last_emit = now


def _duration(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _validation_p40(root: Path, symbol: str) -> Path:
    return root / "reports" / "cross_asset_validation" / f"{symbol}_{PERIOD_TAG}" / "p40"


def discover_sources(root: Path) -> tuple[SignalSource, ...]:
    sources: list[SignalSource] = []
    for symbol in ALL_SYMBOLS:
        source = discover_source(_validation_p40(root, symbol))
        if source.symbol != symbol:
            raise ValueError(f"P40 symbol mismatch: expected {symbol}, got {source.symbol}")
        sources.append(source)
    return tuple(sources)


def load_frozen_signals(sources: tuple[SignalSource, ...]) -> tuple[CoreSignal, ...]:
    signals: list[CoreSignal] = []
    counts: dict[str, int] = {}
    for source in sources:
        current = load_core_signals(source)
        counts[source.symbol] = len(current)
        signals.extend(current)
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"frozen P40 guardrail failed: counts={counts}")
    if len(signals) != EXPECTED_SIGNAL_COUNT:
        raise ValueError(
            f"frozen signal total {len(signals)} != {EXPECTED_SIGNAL_COUNT}"
        )
    unique = {(signal.symbol, signal.touch_at) for signal in signals}
    if len(unique) != len(signals):
        raise ValueError("duplicate frozen Entry keys detected")
    return tuple(sorted(signals, key=lambda signal: (signal.symbol, signal.touch_at)))


def _scenario_name(offset_pct: float) -> str:
    if abs(offset_pct) < 1e-12:
        return "BASELINE_0P00"
    return f"ADVERSE_{offset_pct:.2f}".replace(".", "P")


def _pending_entry_price(direction: Direction, entry_price: float, offset_pct: float) -> float:
    if direction == "Long":
        return entry_price * (1.0 - offset_pct / 100.0)
    return entry_price * (1.0 + offset_pct / 100.0)


def _price_from_signal_move(direction: Direction, entry_price: float, move_pct: float) -> float:
    if direction == "Long":
        return entry_price * (1.0 + move_pct / 100.0)
    return entry_price * (1.0 - move_pct / 100.0)


def _relative_moves_from_signal_moves(
    direction: Direction,
    signal_moves: NDArray[np.float64],
    adverse_offset_pct: float,
) -> NDArray[np.float64]:
    fill_signal_move = -adverse_offset_pct
    denominator = (
        1.0 + fill_signal_move / 100.0
        if direction == "Long"
        else 1.0 - fill_signal_move / 100.0
    )
    if denominator <= 0:
        raise ValueError("invalid shifted-entry denominator")
    return (signal_moves - fill_signal_move) / denominator


def _first_ge(values: NDArray[np.float64], threshold: float) -> int | None:
    indices = np.flatnonzero(values >= threshold)
    return None if indices.size == 0 else int(indices[0])


def _first_le(values: NDArray[np.float64], threshold: float) -> int | None:
    indices = np.flatnonzero(values <= threshold)
    return None if indices.size == 0 else int(indices[0])


def _event_at(path: PathSeries, index: int) -> datetime:
    return datetime.fromtimestamp(path.timestamps[index], UTC)


def _pending_end_index(path: PathSeries, config: Config) -> int:
    end_ts = (path.signal.touch_at + timedelta(hours=config.pending_horizon_hours)).timestamp()
    indices = np.flatnonzero(np.asarray(path.timestamps, dtype=np.float64) <= end_ts)
    return -1 if indices.size == 0 else int(indices[-1])


def _trade_end_index(path: PathSeries, fill_at: datetime, config: Config) -> int:
    end_ts = (fill_at + timedelta(hours=config.trade_horizon_hours)).timestamp()
    indices = np.flatnonzero(np.asarray(path.timestamps, dtype=np.float64) <= end_ts)
    return -1 if indices.size == 0 else int(indices[-1])


def _trade_loader(symbol: str, heartbeat_seconds: float) -> Callable[[Path], TradeDay]:
    def load(path: Path) -> TradeDay:
        return _load_trade_day(
            path,
            progress_label=f"{symbol}/{path.name}",
            heartbeat_seconds=heartbeat_seconds,
            progress_sink=lambda message: print(
                message.replace("[P31 tape]", "[EO1 tape]"),
                flush=True,
            ),
        )

    return load


def _fill_contract(
    path: PathSeries,
    moves: NDArray[np.float64],
    offset_pct: float,
    config: Config,
) -> tuple[
    int | None,
    FillStatus,
    int | None,
    int | None,
    int | None,
    bool,
]:
    pending_end = _pending_end_index(path, config)
    if pending_end < 0:
        return None, "data_end_no_fill", None, None, None, False

    pending_moves = moves[: pending_end + 1]
    pending_complete = path.complete_through >= (
        path.signal.touch_at + timedelta(hours=config.pending_horizon_hours)
    )
    target_index = _first_ge(pending_moves, config.target_pct)

    if abs(offset_pct) < 1e-12:
        return 0, "filled", target_index, 0, 0, pending_complete

    touch_index = _first_le(pending_moves, -offset_pct)
    if touch_index is not None and (target_index is None or touch_index < target_index):
        return touch_index, "filled", target_index, touch_index, touch_index, pending_complete
    if target_index is not None:
        return (
            None,
            "original_target_before_fill",
            target_index,
            touch_index,
            touch_index,
            pending_complete,
        )
    if pending_complete:
        return (
            None,
            "pending_horizon_no_fill",
            None,
            touch_index,
            touch_index,
            pending_complete,
        )
    return None, "data_end_no_fill", None, touch_index, touch_index, pending_complete


def _simulate_trade(
    path: PathSeries,
    moves: NDArray[np.float64],
    fill_index: int,
    offset_pct: float,
    config: Config,
) -> tuple[
    bool,
    int | None,
    int | None,
    ExitReason,
    float,
    float,
    bool,
]:
    fill_at = _event_at(path, fill_index)
    trade_end = _trade_end_index(path, fill_at, config)
    if trade_end < fill_index:
        return False, None, None, "data_end", 0.0, 0.0, False

    signal_suffix = moves[fill_index : trade_end + 1]
    fill_moves = _relative_moves_from_signal_moves(
        path.signal.direction,
        signal_suffix,
        offset_pct,
    )
    target_offset = _first_ge(fill_moves, config.target_pct)
    stop_offset = _first_le(fill_moves, -config.initial_stop_pct)
    activation_offset = _first_ge(fill_moves, config.activation_pct)

    if target_offset is not None and (
        stop_offset is None or target_offset < stop_offset
    ) and (activation_offset is None or target_offset <= activation_offset):
        return (
            False,
            None,
            target_offset,
            "target",
            float(np.max(fill_moves)),
            float(np.min(fill_moves)),
            True,
        )

    if stop_offset is not None and (
        activation_offset is None or stop_offset < activation_offset
    ):
        return (
            False,
            None,
            stop_offset,
            "initial_stop",
            float(np.max(fill_moves)),
            float(np.min(fill_moves)),
            True,
        )

    if activation_offset is not None:
        if target_offset is not None and target_offset == activation_offset:
            return (
                True,
                activation_offset,
                target_offset,
                "target",
                float(np.max(fill_moves)),
                float(np.min(fill_moves)),
                True,
            )
        post_start = activation_offset + 1
        post = fill_moves[post_start:]
        floor_rel = _first_le(post, config.positive_floor_pct)
        target_rel = _first_ge(post, config.target_pct)
        floor_offset = None if floor_rel is None else post_start + floor_rel
        target_after = None if target_rel is None else post_start + target_rel
        if floor_offset is not None and (
            target_after is None or floor_offset < target_after
        ):
            return (
                True,
                activation_offset,
                floor_offset,
                "positive_floor",
                float(np.max(fill_moves)),
                float(np.min(fill_moves)),
                True,
            )
        if target_after is not None:
            return (
                True,
                activation_offset,
                target_after,
                "target",
                float(np.max(fill_moves)),
                float(np.min(fill_moves)),
                True,
            )

    trade_complete = path.complete_through >= (
        fill_at + timedelta(hours=config.trade_horizon_hours)
    )
    return (
        activation_offset is not None,
        activation_offset,
        None,
        "horizon" if trade_complete else "data_end",
        float(np.max(fill_moves)),
        float(np.min(fill_moves)),
        trade_complete,
    )


def analyze_path(path: PathSeries, config: Config) -> list[EventResult]:
    if not path.timestamps:
        raise ValueError(f"no observations for {path.signal.symbol} {path.signal.touch_at}")
    moves = np.asarray(path.moves_pct, dtype=np.float64)
    results: list[EventResult] = []

    for offset_pct in ENTRY_ADVERSE_OFFSETS_PCT:
        (
            fill_index,
            fill_status,
            target_before_fill_index,
            touch_any_index,
            _,
            pending_complete,
        ) = _fill_contract(path, moves, offset_pct, config)
        pending_price = _pending_entry_price(
            path.signal.direction,
            path.signal.entry_price,
            offset_pct,
        )
        target_before_fill_at = (
            _event_at(path, target_before_fill_index)
            if fill_status == "original_target_before_fill"
            and target_before_fill_index is not None
            else None
        )
        touch_any_at = _event_at(path, touch_any_index) if touch_any_index is not None else None

        if fill_index is None:
            results.append(
                EventResult(
                    symbol=path.signal.symbol,
                    direction=path.signal.direction,
                    touch_at=path.signal.touch_at,
                    original_entry_price=path.signal.entry_price,
                    adverse_offset_pct=offset_pct,
                    scenario=_scenario_name(offset_pct),
                    pending_entry_price=pending_price,
                    fill_status=fill_status,
                    fill_at=None,
                    fill_price_ideal=None,
                    first_cross_signal_move_pct=None,
                    first_cross_price=None,
                    trigger_slippage_bps_proxy=None,
                    seconds_touch_to_fill=None,
                    original_target_before_fill_at=target_before_fill_at,
                    offset_touched_anytime_pending_72h=touch_any_index is not None,
                    offset_touch_anytime_at=touch_any_at,
                    protection_activated=False,
                    protection_activation_at=None,
                    exit_reason=None,
                    exit_at=None,
                    exit_move_from_fill_pct_observed=None,
                    theoretical_exit_level_pct=None,
                    gross_pnl_pct_theoretical=None,
                    net_pnl_pct_after_cost_reserve=None,
                    pnl_usd_100_margin_10x=None,
                    mfe_from_fill_pct=None,
                    mae_from_fill_pct=None,
                    pending_window_complete=pending_complete,
                    trade_window_complete=None,
                    missing_archive_days=";".join(path.missing_archive_days),
                )
            )
            continue

        first_cross_signal_move = float(moves[fill_index])
        first_cross_price = _price_from_signal_move(
            path.signal.direction,
            path.signal.entry_price,
            first_cross_signal_move,
        )
        slippage_proxy = directional_move_pct(
            path.signal.direction,
            pending_price,
            first_cross_price,
        ) * 100.0
        fill_at = _event_at(path, fill_index)
        seconds_to_fill = max(0.0, path.timestamps[fill_index] - path.signal.touch_at.timestamp())
        (
            activated,
            activation_offset,
            exit_offset,
            exit_reason,
            mfe,
            mae,
            trade_complete,
        ) = _simulate_trade(path, moves, fill_index, offset_pct, config)
        activation_at = (
            _event_at(path, fill_index + activation_offset)
            if activation_offset is not None
            else None
        )
        exit_at: datetime | None = None
        exit_move_observed: float | None = None
        theoretical_level: float | None = None
        gross_pct: float | None = None
        net_pct: float | None = None
        pnl_usd: float | None = None
        if exit_offset is not None:
            absolute_exit = fill_index + exit_offset
            exit_at = _event_at(path, absolute_exit)
            signal_move = float(moves[absolute_exit])
            exit_price = _price_from_signal_move(
                path.signal.direction,
                path.signal.entry_price,
                signal_move,
            )
            exit_move_observed = directional_move_pct(
                path.signal.direction,
                pending_price,
                exit_price,
            )
            if exit_reason == "target":
                theoretical_level = config.target_pct
            elif exit_reason == "initial_stop":
                theoretical_level = -config.initial_stop_pct
            elif exit_reason == "positive_floor":
                theoretical_level = config.positive_floor_pct
            if theoretical_level is not None:
                gross_pct = theoretical_level
                net_pct = gross_pct - config.illustrative_round_trip_cost_pct
                notional = config.margin_usd * config.leverage
                pnl_usd = notional * net_pct / 100.0

        results.append(
            EventResult(
                symbol=path.signal.symbol,
                direction=path.signal.direction,
                touch_at=path.signal.touch_at,
                original_entry_price=path.signal.entry_price,
                adverse_offset_pct=offset_pct,
                scenario=_scenario_name(offset_pct),
                pending_entry_price=pending_price,
                fill_status=fill_status,
                fill_at=fill_at,
                fill_price_ideal=pending_price,
                first_cross_signal_move_pct=first_cross_signal_move,
                first_cross_price=first_cross_price,
                trigger_slippage_bps_proxy=slippage_proxy,
                seconds_touch_to_fill=seconds_to_fill,
                original_target_before_fill_at=None,
                offset_touched_anytime_pending_72h=True,
                offset_touch_anytime_at=touch_any_at,
                protection_activated=activated,
                protection_activation_at=activation_at,
                exit_reason=exit_reason,
                exit_at=exit_at,
                exit_move_from_fill_pct_observed=exit_move_observed,
                theoretical_exit_level_pct=theoretical_level,
                gross_pnl_pct_theoretical=gross_pct,
                net_pnl_pct_after_cost_reserve=net_pct,
                pnl_usd_100_margin_10x=pnl_usd,
                mfe_from_fill_pct=mfe,
                mae_from_fill_pct=mae,
                pending_window_complete=pending_complete,
                trade_window_complete=trade_complete,
                missing_archive_days=";".join(path.missing_archive_days),
            )
        )
    return results


def _scope_symbols(scope: str) -> tuple[str, ...]:
    if scope == "ALL9":
        return ALL_SYMBOLS
    if scope == "DEV2":
        return DEV_SYMBOLS
    if scope == "HOLDOUT7":
        return HOLDOUT_SYMBOLS
    if scope in ALL_SYMBOLS:
        return (scope,)
    raise ValueError(f"unknown scope: {scope}")


def _median(values: Sequence[float]) -> float | None:
    return None if not values else float(statistics.median(values))


def summarize(rows: Sequence[EventResult], scope: str) -> list[dict[str, Any]]:
    symbols = set(_scope_symbols(scope))
    scoped = [row for row in rows if row.symbol in symbols]
    output: list[dict[str, Any]] = []
    for offset_pct in ENTRY_ADVERSE_OFFSETS_PCT:
        items = [row for row in scoped if abs(row.adverse_offset_pct - offset_pct) < 1e-12]
        filled = [row for row in items if row.fill_status == "filled"]
        target_before_fill = [
            row for row in items if row.fill_status == "original_target_before_fill"
        ]
        pending_no_fill = [
            row for row in items if row.fill_status == "pending_horizon_no_fill"
        ]
        data_end_no_fill = [row for row in items if row.fill_status == "data_end_no_fill"]
        touched_anytime = [row for row in items if row.offset_touched_anytime_pending_72h]
        targets = [row for row in filled if row.exit_reason == "target"]
        initial_stops = [row for row in filled if row.exit_reason == "initial_stop"]
        floors = [row for row in filled if row.exit_reason == "positive_floor"]
        horizons = [row for row in filled if row.exit_reason == "horizon"]
        data_end = [row for row in filled if row.exit_reason == "data_end"]
        activated = [row for row in filled if row.protection_activated]
        resolved = [
            row
            for row in filled
            if row.exit_reason in {"target", "initial_stop", "positive_floor"}
        ]
        pnl_values = [
            float(row.pnl_usd_100_margin_10x)
            for row in resolved
            if row.pnl_usd_100_margin_10x is not None
        ]
        profits = sum(value for value in pnl_values if value > 0)
        losses = -sum(value for value in pnl_values if value < 0)
        pf: float | None
        if losses > 0:
            pf = profits / losses
        elif profits > 0:
            pf = float("inf")
        else:
            pf = None
        fill_times = [
            float(row.seconds_touch_to_fill)
            for row in filled
            if row.seconds_touch_to_fill is not None
        ]
        signals = len(items)
        output.append(
            {
                "scope": scope,
                "adverse_offset_pct": offset_pct,
                "scenario": _scenario_name(offset_pct),
                "signals": signals,
                "filled": len(filled),
                "fill_rate_pct": 100.0 * len(filled) / signals if signals else 0.0,
                "offset_touched_anytime_pending_72h": len(touched_anytime),
                "offset_touch_anytime_rate_pct": (
                    100.0 * len(touched_anytime) / signals if signals else 0.0
                ),
                "original_target_before_fill": len(target_before_fill),
                "pending_horizon_no_fill": len(pending_no_fill),
                "data_end_no_fill": len(data_end_no_fill),
                "protection_activated": len(activated),
                "activation_rate_per_fill_pct": (
                    100.0 * len(activated) / len(filled) if filled else 0.0
                ),
                "target_plus_1p10": len(targets),
                "target_rate_per_fill_pct": (
                    100.0 * len(targets) / len(filled) if filled else 0.0
                ),
                "target_rate_per_signal_pct": (
                    100.0 * len(targets) / signals if signals else 0.0
                ),
                "initial_stop_minus_1p00": len(initial_stops),
                "positive_floor_plus_0p10": len(floors),
                "horizon_open": len(horizons),
                "data_end_open": len(data_end),
                "resolved_trades": len(resolved),
                "median_seconds_touch_to_fill": _median(fill_times),
                "aggregate_net_usd_fixed_100_margin_10x": sum(pnl_values),
                "ev_usd_per_filled_trade": (
                    sum(pnl_values) / len(resolved) if resolved else None
                ),
                "ev_usd_per_original_signal": (
                    sum(pnl_values) / signals if signals else None
                ),
                "profit_factor": pf,
            }
        )
    return output


def _temporal_fold(touch_at: datetime) -> str:
    for name, start, end in TEMPORAL_FOLDS:
        if start <= touch_at < end:
            return name
    return "OUTSIDE"


def _events_csv(path: Path, rows: Sequence[EventResult]) -> None:
    fieldnames = list(asdict(rows[0]).keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            raw = asdict(row)
            writer.writerow(
                {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in raw.items()
                }
            )


def _records_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_contract(sources: tuple[SignalSource, ...], config: Config) -> dict[str, Any]:
    source_rows = []
    for source in sources:
        source_rows.append(
            {
                "symbol": source.symbol,
                "features_path": str(source.features_path),
                "features_sha256": _sha256(source.features_path),
                "summary_path": str(source.summary_path),
                "summary_sha256": _sha256(source.summary_path),
                "dataset_dir": str(source.dataset_dir),
            }
        )
    payload: dict[str, Any] = {
        "research_version": RESEARCH_VERSION,
        "period_tag": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "expected_signal_count": EXPECTED_SIGNAL_COUNT,
        "adverse_offsets_pct": list(ENTRY_ADVERSE_OFFSETS_PCT),
        "fill_rule": (
            "Long buys below original Entry; Short sells above original Entry. "
            "Pending order is cancelled if original +1.10 target occurs first."
        ),
        "exit_rule": (
            "All stop/activation/floor/target levels are anchored to the actual shifted fill: "
            "-1.00 initial stop; activate at +0.10; then +0.10 floor; target +1.10."
        ),
        "config": asdict(config),
        "sources": source_rows,
        "downloads": "DISABLED",
        "production_effect": "NONE",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    payload["contract_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def _row_to_json(row: EventResult) -> str:
    raw = asdict(row)
    serializable = {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in raw.items()
    }
    return json.dumps(serializable, separators=(",", ":"), ensure_ascii=False)


def _row_from_json(line: str) -> EventResult:
    raw = json.loads(line)
    datetime_fields = {
        "touch_at",
        "fill_at",
        "original_target_before_fill_at",
        "offset_touch_anytime_at",
        "protection_activation_at",
        "exit_at",
    }
    for key in datetime_fields:
        if raw.get(key) is not None:
            raw[key] = datetime.fromisoformat(str(raw[key]))
    return EventResult(**raw)


def _load_partial(path: Path) -> tuple[list[EventResult], set[tuple[str, str]]]:
    if not path.exists():
        return [], set()
    rows: list[EventResult] = []
    counts: dict[tuple[str, str], int] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = _row_from_json(line)
            rows.append(row)
            key = (row.symbol, row.touch_at.isoformat())
            counts[key] = counts.get(key, 0) + 1
    expected_per_signal = len(ENTRY_ADVERSE_OFFSETS_PCT)
    bad = {key: count for key, count in counts.items() if count != expected_per_signal}
    if bad:
        raise ValueError(f"partial file has incomplete signal blocks: {bad}")
    return rows, set(counts)


def _summary_markdown(summary: Sequence[dict[str, Any]], contract_hash: str) -> str:
    all9 = [row for row in summary if row["scope"] == "ALL9"]
    lines = [
        "# EO1 Adverse Entry Offset Replay V1",
        "",
        "Research only. Existing Entry/Exit/Risk/Execution/live code is unchanged.",
        "",
        f"Run contract SHA256: `{contract_hash}`",
        "",
        "Primary question: what happens if the existing frozen Entry is replaced by a pending",
        "entry 0.10% or 0.20% against trade direction, while exit levels are re-anchored to",
        "the actually filled delayed entry.",
        "",
        "## ALL9",
        "",
        (
            "| Offset against trade | Filled | Fill % | Original +1.10 before fill | "
            "+0.10 activated | +1.10 target | +1.10/fill | -1.00 stop | +0.10 floor | "
            "EV/fill $100x10 | EV/signal $100x10 | PF |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in all9:
        pf = row["profit_factor"]
        pf_text = "inf" if pf == float("inf") else "" if pf is None else f"{float(pf):.3f}"
        ev_fill = row["ev_usd_per_filled_trade"]
        ev_signal = row["ev_usd_per_original_signal"]
        lines.append(
            f"| {float(row['adverse_offset_pct']):.2f}% | {row['filled']} | "
            f"{float(row['fill_rate_pct']):.2f}% | {row['original_target_before_fill']} | "
            f"{row['protection_activated']} | {row['target_plus_1p10']} | "
            f"{float(row['target_rate_per_fill_pct']):.2f}% | "
            f"{row['initial_stop_minus_1p00']} | {row['positive_floor_plus_0p10']} | "
            f"{'' if ev_fill is None else f'{float(ev_fill):.3f}'} | "
            f"{'' if ev_signal is None else f'{float(ev_signal):.3f}'} | {pf_text} |"
        )
    lines.extend(
        [
            "",
            "## Contract",
            "",
            "- Frozen ALL9 exact-touch Entry signals: 1063.",
            "- Long delayed entries: original Entry -0.10% and -0.20%.",
            "- Short delayed entries: original Entry +0.10% and +0.20%.",
            "- Pending entry expires after 72h and is cancelled earlier if the original signal",
            "  reaches +1.10% before the delayed price. The report also records whether that",
            "  delayed price was touched later inside the pending 72h window.",
            "- After fill, all levels are relative to the new fill: -1.00% initial stop;",
            "  activate protection at +0.10%; then theoretical +0.10% floor; target +1.10%.",
            "- Trade observation horizon is 72h from the actual delayed fill.",
            "- +0.10% is a theoretical price floor, not guaranteed economic break-even after",
            "  fees/slippage. Economics uses an illustrative 0.10% notional round-trip reserve.",
            "- Signal replay only; not a portfolio backtest.",
            "",
        ]
    )
    return "\n".join(lines)


def run(project_root: Path, output_dir: Path, config: Config) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = discover_sources(project_root)
    signals = load_frozen_signals(sources)
    contract = _run_contract(sources, config)
    contract_path = output_dir / "run_contract.json"
    partial_path = output_dir / "entry_offset_adverse_events.partial.jsonl"
    if contract_path.exists():
        existing = json.loads(contract_path.read_text(encoding="utf-8"))
        if existing != contract:
            raise ValueError("resume contract mismatch; use a fresh output directory")
    else:
        contract_path.write_text(
            json.dumps(contract, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    existing_rows, completed = _load_partial(partial_path)
    rows = list(existing_rows)
    archive_maps = {source.symbol: _archive_map(source.dataset_dir) for source in sources}
    caches = {
        symbol: TradeDayCache(
            max_days=config.day_cache_size,
            loader=_trade_loader(symbol, config.progress_interval_seconds),
        )
        for symbol in ALL_SYMBOLS
    }
    progress = Progress(config.progress_interval_seconds)
    progress.emit(len(completed), len(signals), force=True, detail="resume-aware")
    processed = len(completed)

    with partial_path.open("a", encoding="utf-8") as partial:
        for signal in signals:
            key = (signal.symbol, signal.touch_at.isoformat())
            if key in completed:
                continue
            path = build_path_series(
                signal,
                archive_maps[signal.symbol],
                horizon_hours=config.max_path_hours,
                cache=caches[signal.symbol],
            )
            available_days = sorted(archive_maps[signal.symbol])
            max_day = available_days[-1] if available_days else ""
            internal_missing = [day for day in path.missing_archive_days if day <= max_day]
            if internal_missing:
                raise FileNotFoundError(
                    "internal archive gap for "
                    f"{signal.symbol} {signal.touch_at}: {internal_missing}"
                )
            signal_rows = analyze_path(path, config)
            for row in signal_rows:
                partial.write(_row_to_json(row) + "\n")
                rows.append(row)
            partial.flush()
            processed += 1
            progress.emit(
                processed,
                len(signals),
                detail=f"{signal.symbol} {signal.touch_at.isoformat()}",
            )
    progress.emit(len(signals), len(signals), force=True, detail="complete")

    expected_rows = len(signals) * len(ENTRY_ADVERSE_OFFSETS_PCT)
    if len(rows) != expected_rows:
        raise ValueError(f"result row count mismatch: {len(rows)} != {expected_rows}")

    events_path = output_dir / "entry_offset_adverse_events.csv"
    summary_path = output_dir / "entry_offset_adverse_summary.csv"
    temporal_path = output_dir / "entry_offset_adverse_temporal.csv"
    _events_csv(events_path, rows)
    scopes = ["ALL9", "DEV2", "HOLDOUT7", *ALL_SYMBOLS]
    summary = [record for scope in scopes for record in summarize(rows, scope)]
    _records_csv(summary_path, summary)

    temporal_records: list[dict[str, Any]] = []
    for fold in ("F1", "F2", "F3"):
        fold_rows = [row for row in rows if _temporal_fold(row.touch_at) == fold]
        for record in summarize(fold_rows, "ALL9"):
            temporal_records.append({"fold": fold, **record})
    _records_csv(temporal_path, temporal_records)

    summary_payload: dict[str, Any] = {
        "research": RESEARCH_VERSION,
        "completed_at": datetime.now(UTC).isoformat(),
        "run_contract_sha256": contract["contract_sha256"],
        "signals": len(signals),
        "event_rows": len(rows),
        "adverse_offsets_pct": list(ENTRY_ADVERSE_OFFSETS_PCT),
        "target_pct": config.target_pct,
        "initial_stop_pct": config.initial_stop_pct,
        "activation_pct": config.activation_pct,
        "positive_floor_pct": config.positive_floor_pct,
        "downloads": "DISABLED",
        "production_effect": "NONE",
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(
        _summary_markdown(summary, str(contract["contract_sha256"])),
        encoding="utf-8",
    )
    provenance = {
        **summary_payload,
        "event_table_sha256": _sha256(events_path),
        "summary_table_sha256": _sha256(summary_path),
        "temporal_table_sha256": _sha256(temporal_path),
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EO1 adverse delayed-entry replay on frozen ALL9 signals"
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    parser.add_argument("--day-cache-size", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config = Config(
        progress_interval_seconds=float(args.progress_interval_seconds),
        day_cache_size=int(args.day_cache_size),
    )
    run(args.project_root.resolve(), args.output_dir.resolve(), config)


if __name__ == "__main__":
    main()
