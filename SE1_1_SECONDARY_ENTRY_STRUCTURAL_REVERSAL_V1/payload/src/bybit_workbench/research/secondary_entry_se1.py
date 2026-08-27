from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

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
from bybit_workbench.research.flow_reversal_v1 import (
    TradeDay,
    _archive_map,
    _load_trade_day,
)
from bybit_workbench.research.mtf_entry import Direction

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
EXPECTED_ALL9 = 1063

DEFAULT_MIN_ADVERSE_DEPTHS_PCT = (0.10, 0.25, 0.50, 0.75)
DEFAULT_REBOUND_CONFIRMATIONS_PCT = (0.10, 0.15, 0.20, 0.25, 0.30)
DEFAULT_TARGETS_PCT = (0.10, 0.20, 0.25, 0.30, 0.50, 1.00, 1.10, 2.00, 3.00, 5.00)
DEFAULT_PROTECTION_ACTIVATIONS_PCT = (0.20, 0.25, 0.30, 0.50)

TriggerStatus = Literal["triggered", "main_stop_before_trigger", "no_trigger", "data_end"]
ExitReason = Literal["structural_stop", "horizon", "data_end"]
ProtectionExitReason = Literal[
    "structural_stop_before_activation",
    "positive_floor",
    "horizon",
    "data_end",
]


@dataclass(frozen=True, slots=True)
class Config:
    main_stop_pct: float = 1.00
    structural_buffer_pct: float = 0.10
    horizon_hours: int = 72
    min_adverse_depths_pct: tuple[float, ...] = DEFAULT_MIN_ADVERSE_DEPTHS_PCT
    rebound_confirmations_pct: tuple[float, ...] = DEFAULT_REBOUND_CONFIRMATIONS_PCT
    targets_pct: tuple[float, ...] = DEFAULT_TARGETS_PCT
    protection_activations_pct: tuple[float, ...] = DEFAULT_PROTECTION_ACTIVATIONS_PCT
    positive_floor_pct: float = 0.10
    day_cache_size: int = 6
    progress_interval_seconds: float = 20.0
    scale_margin_usd: float = 100.0
    scale_leverage: float = 10.0
    illustrative_round_trip_cost_pct: float = 0.10
    diagnostic_fixed_risk_usd: float = 2.0

    def __post_init__(self) -> None:
        if self.main_stop_pct <= 0:
            raise ValueError("main_stop_pct must be positive")
        if self.structural_buffer_pct <= 0:
            raise ValueError("structural_buffer_pct must be positive")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if any(value <= 0 or value >= self.main_stop_pct for value in self.min_adverse_depths_pct):
            raise ValueError("min adverse depths must be inside (0, main_stop_pct)")
        if any(value <= 0 for value in self.rebound_confirmations_pct):
            raise ValueError("rebound confirmations must be positive")
        if any(value <= 0 for value in self.targets_pct):
            raise ValueError("targets must be positive")
        if any(value <= self.positive_floor_pct for value in self.protection_activations_pct):
            raise ValueError("protection activation must exceed positive floor")
        if self.positive_floor_pct < 0:
            raise ValueError("positive_floor_pct cannot be negative")
        if self.day_cache_size <= 0 or self.progress_interval_seconds <= 0:
            raise ValueError("cache/progress settings must be positive")
        if self.scale_margin_usd <= 0 or self.scale_leverage <= 0:
            raise ValueError("scale margin/leverage must be positive")
        if self.illustrative_round_trip_cost_pct < 0:
            raise ValueError("illustrative cost cannot be negative")
        if self.diagnostic_fixed_risk_usd <= 0:
            raise ValueError("diagnostic_fixed_risk_usd must be positive")


@dataclass(frozen=True, slots=True)
class TriggerKey:
    min_adverse_depth_pct: float
    rebound_confirmation_pct: float

    @property
    def key(self) -> str:
        return (
            f"adverse_{self.min_adverse_depth_pct:.2f}_"
            f"rebound_{self.rebound_confirmation_pct:.2f}"
        )


@dataclass(frozen=True, slots=True)
class TriggerEvent:
    symbol: str
    direction: Direction
    touch_at: datetime
    main_entry_price: float
    min_adverse_depth_pct: float
    rebound_confirmation_pct: float
    trigger_status: TriggerStatus
    launch_at: datetime | None
    launch_move_vs_main_pct: float | None
    launch_price: float | None
    scale_entry_at: datetime | None
    scale_entry_price: float | None
    scale_entry_move_vs_main_pct: float | None
    scale_entry_rebound_from_launch_pct: float | None
    structural_stop_move_vs_main_pct: float | None
    structural_stop_price: float | None
    structural_stop_distance_from_scale_pct: float | None
    structural_stop_risk_usd_fixed_notional: float | None
    illustrative_round_trip_cost_usd: float | None
    structural_stop_loss_usd_with_cost_reserve: float | None
    notional_for_diagnostic_fixed_risk_usd: float | None
    margin_at_10x_for_diagnostic_fixed_risk_usd: float | None
    main_stop_at: datetime | None
    zero_crossings_before_scale: int
    seconds_touch_to_launch: float | None
    seconds_launch_to_scale: float | None
    seconds_touch_to_scale: float | None
    complete_horizon: bool
    missing_archive_days: str
    secondary_exit_reason: ExitReason | None
    secondary_exit_at: datetime | None
    secondary_exit_move_pct: float | None
    secondary_mfe_to_exit_pct: float | None
    secondary_mae_to_exit_pct: float | None
    secondary_mfe_to_horizon_pct: float | None
    secondary_mae_to_horizon_pct: float | None
    target_hits_json: str
    protection_results_json: str


class Progress:
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
            f"[SE1] stage={stage} processed={processed}/{total} "
            f"({100.0 * processed / max(1, total):.1f}%) elapsed={_duration(elapsed)} "
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
    if len(signals) != EXPECTED_ALL9:
        raise ValueError(f"frozen signal total {len(signals)} != {EXPECTED_ALL9}")
    unique = {(signal.symbol, signal.touch_at) for signal in signals}
    if len(unique) != len(signals):
        raise ValueError("duplicate frozen Entry keys detected")
    return tuple(sorted(signals, key=lambda item: (item.symbol, item.touch_at)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_contract(sources: tuple[SignalSource, ...], config: Config) -> dict[str, Any]:
    source_rows: list[dict[str, str]] = []
    for source in sources:
        manifest = source.dataset_dir / "dataset_manifest.json"
        source_rows.append(
            {
                "symbol": source.symbol,
                "p40_summary_sha256": _sha256(source.summary_path),
                "p40_features_sha256": _sha256(source.features_path),
                "dataset_manifest_sha256": _sha256(manifest),
            }
        )
    contract: dict[str, Any] = {
        "research": "SE1_SECONDARY_ENTRY_STRUCTURAL_REVERSAL_V1",
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "expected_counts": EXPECTED_COUNTS,
        "config": asdict(config),
        "sources": source_rows,
    }
    canonical = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    contract["contract_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return contract


def _price_from_main_move(direction: Direction, main_entry_price: float, move_pct: float) -> float:
    if direction == "Long":
        return main_entry_price * (1.0 + move_pct / 100.0)
    return main_entry_price * (1.0 - move_pct / 100.0)


def _secondary_move_pct(direction: Direction, scale_entry_price: float, price: float) -> float:
    return directional_move_pct(direction, scale_entry_price, price)


def _event_at(path: PathSeries, index: int) -> datetime:
    return datetime.fromtimestamp(path.timestamps[index], UTC)


def _zero_crossings(moves: tuple[float, ...], end_index: int) -> int:
    crossings = 0
    prior_sign = 0
    for move in moves[: end_index + 1]:
        sign = 1 if move > 0 else -1 if move < 0 else 0
        if sign == 0:
            continue
        if prior_sign and sign != prior_sign:
            crossings += 1
        prior_sign = sign
    return crossings


def _first_index(mask: NDArray[np.bool_]) -> int | None:
    indices = np.flatnonzero(mask)
    if indices.size == 0:
        return None
    return int(indices[0])


def _running_minimum(
    moves: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.int64]]:
    running_min = np.minimum.accumulate(moves)
    new_low = np.empty(moves.shape, dtype=np.bool_)
    new_low[0] = True
    if moves.size > 1:
        new_low[1:] = running_min[1:] < running_min[:-1]
    indices = np.arange(moves.size, dtype=np.int64)
    low_indices = np.where(new_low, indices, 0)
    running_argmin = np.maximum.accumulate(low_indices)
    return running_min, running_argmin


def _secondary_moves_from_main_moves(
    direction: Direction,
    main_moves: NDArray[np.float64],
    scale_move_vs_main_pct: float,
) -> NDArray[np.float64]:
    denominator = (
        1.0 + scale_move_vs_main_pct / 100.0
        if direction == "Long"
        else 1.0 - scale_move_vs_main_pct / 100.0
    )
    if denominator <= 0:
        raise ValueError("invalid scale-entry denominator")
    return (main_moves - scale_move_vs_main_pct) / denominator


def _target_hits_from_secondary_moves(
    path: PathSeries,
    scale_index: int,
    secondary_moves: NDArray[np.float64],
    targets: tuple[float, ...],
    *,
    exit_offset: int | None,
) -> dict[str, dict[str, float | str] | None]:
    last_offset = secondary_moves.size - 1 if exit_offset is None else exit_offset
    active = secondary_moves[: last_offset + 1]
    result: dict[str, dict[str, float | str] | None] = {}
    start_ts = path.timestamps[scale_index]
    for target in targets:
        offset = _first_index(active >= target)
        if offset is None:
            result[f"{target:.2f}"] = None
            continue
        absolute_index = scale_index + offset
        result[f"{target:.2f}"] = {
            "at": _event_at(path, absolute_index).isoformat(),
            "seconds": round(path.timestamps[absolute_index] - start_ts, 6),
        }
    return result


def _protection_from_secondary_moves(
    path: PathSeries,
    scale_index: int,
    secondary_moves: NDArray[np.float64],
    structural_stop_offset: int | None,
    activation_pct: float,
    floor_pct: float,
    *,
    complete_horizon: bool,
) -> dict[str, Any]:
    pre_stop_end = (
        secondary_moves.size
        if structural_stop_offset is None
        else structural_stop_offset + 1
    )
    activation_offset = _first_index(secondary_moves[:pre_stop_end] >= activation_pct)
    start_ts = path.timestamps[scale_index]

    exit_offset: int | None = None
    exit_reason: ProtectionExitReason
    if activation_offset is None:
        if structural_stop_offset is not None:
            exit_offset = structural_stop_offset
            exit_reason = "structural_stop_before_activation"
        else:
            exit_reason = "horizon" if complete_horizon else "data_end"
    else:
        floor_relative = _first_index(
            secondary_moves[activation_offset + 1 :] <= floor_pct
        )
        if floor_relative is None:
            exit_reason = "horizon" if complete_horizon else "data_end"
        else:
            exit_offset = activation_offset + 1 + floor_relative
            exit_reason = "positive_floor"

    mfe_end = secondary_moves.size if exit_offset is None else exit_offset + 1
    mfe_before_exit = float(np.max(secondary_moves[:mfe_end]))
    activation_at = (
        _event_at(path, scale_index + activation_offset)
        if activation_offset is not None
        else None
    )
    exit_at = (
        _event_at(path, scale_index + exit_offset)
        if exit_offset is not None
        else None
    )
    return {
        "activation_pct": activation_pct,
        "floor_pct": floor_pct,
        "activated": activation_offset is not None,
        "activation_at": activation_at.isoformat() if activation_at is not None else None,
        "activation_seconds": (
            round(path.timestamps[scale_index + activation_offset] - start_ts, 6)
            if activation_offset is not None
            else None
        ),
        "exit_reason": exit_reason,
        "exit_at": exit_at.isoformat() if exit_at is not None else None,
        "mfe_before_exit_pct": mfe_before_exit,
        "exit_move_pct": (
            float(secondary_moves[exit_offset]) if exit_offset is not None else None
        ),
    }


def analyze_signal(path: PathSeries, config: Config) -> list[TriggerEvent]:
    if not path.timestamps:
        raise ValueError(
            "no trade observations at/after Entry: "
            f"{path.signal.symbol} {path.signal.touch_at}"
        )

    moves = np.asarray(path.moves_pct, dtype=np.float64)
    running_min, running_argmin = _running_minimum(moves)
    main_stop_index = _first_index(moves <= -config.main_stop_pct)
    main_stop_at = _event_at(path, main_stop_index) if main_stop_index is not None else None
    complete = path.complete_through >= path.signal.touch_at + timedelta(
        hours=config.horizon_hours
    )
    trigger_limit = moves.size if main_stop_index is None else main_stop_index
    notional = config.scale_margin_usd * config.scale_leverage
    cost_reserve = notional * config.illustrative_round_trip_cost_pct / 100.0
    rows: list[TriggerEvent] = []

    for min_adverse in config.min_adverse_depths_pct:
        for rebound in config.rebound_confirmations_pct:
            trigger_mask = (
                (running_min[:trigger_limit] <= -min_adverse)
                & (
                    moves[:trigger_limit] - running_min[:trigger_limit]
                    >= rebound
                )
            )
            scale_index = _first_index(trigger_mask)
            if scale_index is None:
                status: TriggerStatus
                if main_stop_index is not None:
                    status = "main_stop_before_trigger"
                else:
                    status = "no_trigger" if complete else "data_end"
                rows.append(
                    TriggerEvent(
                        symbol=path.signal.symbol,
                        direction=path.signal.direction,
                        touch_at=path.signal.touch_at,
                        main_entry_price=path.signal.entry_price,
                        min_adverse_depth_pct=min_adverse,
                        rebound_confirmation_pct=rebound,
                        trigger_status=status,
                        launch_at=None,
                        launch_move_vs_main_pct=None,
                        launch_price=None,
                        scale_entry_at=None,
                        scale_entry_price=None,
                        scale_entry_move_vs_main_pct=None,
                        scale_entry_rebound_from_launch_pct=None,
                        structural_stop_move_vs_main_pct=None,
                        structural_stop_price=None,
                        structural_stop_distance_from_scale_pct=None,
                        structural_stop_risk_usd_fixed_notional=None,
                        illustrative_round_trip_cost_usd=None,
                        structural_stop_loss_usd_with_cost_reserve=None,
                        notional_for_diagnostic_fixed_risk_usd=None,
                        margin_at_10x_for_diagnostic_fixed_risk_usd=None,
                        main_stop_at=main_stop_at,
                        zero_crossings_before_scale=0,
                        seconds_touch_to_launch=None,
                        seconds_launch_to_scale=None,
                        seconds_touch_to_scale=None,
                        complete_horizon=complete,
                        missing_archive_days=";".join(path.missing_archive_days),
                        secondary_exit_reason=None,
                        secondary_exit_at=None,
                        secondary_exit_move_pct=None,
                        secondary_mfe_to_exit_pct=None,
                        secondary_mae_to_exit_pct=None,
                        secondary_mfe_to_horizon_pct=None,
                        secondary_mae_to_horizon_pct=None,
                        target_hits_json="{}",
                        protection_results_json="{}",
                    )
                )
                continue

            launch_index = int(running_argmin[scale_index])
            launch_move = float(moves[launch_index])
            scale_move_main = float(moves[scale_index])
            launch_price = _price_from_main_move(
                path.signal.direction,
                path.signal.entry_price,
                launch_move,
            )
            scale_price = _price_from_main_move(
                path.signal.direction,
                path.signal.entry_price,
                scale_move_main,
            )
            structural_stop_move_main = launch_move - config.structural_buffer_pct
            structural_stop_price = _price_from_main_move(
                path.signal.direction,
                path.signal.entry_price,
                structural_stop_move_main,
            )
            stop_distance = abs(
                _secondary_move_pct(
                    path.signal.direction,
                    scale_price,
                    structural_stop_price,
                )
            )
            risk_usd = notional * stop_distance / 100.0
            notional_for_fixed_risk = (
                config.diagnostic_fixed_risk_usd * 100.0 / stop_distance
                if stop_distance > 0
                else None
            )
            margin_for_fixed_risk = (
                notional_for_fixed_risk / config.scale_leverage
                if notional_for_fixed_risk is not None
                else None
            )

            main_suffix = moves[scale_index:]
            secondary_moves = _secondary_moves_from_main_moves(
                path.signal.direction,
                main_suffix,
                scale_move_main,
            )
            structural_stop_offset = _first_index(
                main_suffix <= structural_stop_move_main
            )
            exit_slice_end = (
                secondary_moves.size
                if structural_stop_offset is None
                else structural_stop_offset + 1
            )
            active_secondary = secondary_moves[:exit_slice_end]
            mfe_to_exit = float(np.max(active_secondary))
            mae_to_exit = float(np.min(active_secondary))
            mfe_to_horizon = float(np.max(secondary_moves))
            mae_to_horizon = float(np.min(secondary_moves))
            if structural_stop_offset is not None:
                exit_reason: ExitReason = "structural_stop"
                exit_index = scale_index + structural_stop_offset
                exit_move = float(secondary_moves[structural_stop_offset])
            else:
                exit_reason = "horizon" if complete else "data_end"
                exit_index = None
                exit_move = None

            target_hits = _target_hits_from_secondary_moves(
                path,
                scale_index,
                secondary_moves,
                config.targets_pct,
                exit_offset=structural_stop_offset,
            )
            protections = {
                (
                    f"activate_{activation:.2f}_"
                    f"floor_{config.positive_floor_pct:.2f}"
                ): _protection_from_secondary_moves(
                    path,
                    scale_index,
                    secondary_moves,
                    structural_stop_offset,
                    activation,
                    config.positive_floor_pct,
                    complete_horizon=complete,
                )
                for activation in config.protection_activations_pct
            }
            rows.append(
                TriggerEvent(
                    symbol=path.signal.symbol,
                    direction=path.signal.direction,
                    touch_at=path.signal.touch_at,
                    main_entry_price=path.signal.entry_price,
                    min_adverse_depth_pct=min_adverse,
                    rebound_confirmation_pct=rebound,
                    trigger_status="triggered",
                    launch_at=_event_at(path, launch_index),
                    launch_move_vs_main_pct=launch_move,
                    launch_price=launch_price,
                    scale_entry_at=_event_at(path, scale_index),
                    scale_entry_price=scale_price,
                    scale_entry_move_vs_main_pct=scale_move_main,
                    scale_entry_rebound_from_launch_pct=(
                        scale_move_main - launch_move
                    ),
                    structural_stop_move_vs_main_pct=structural_stop_move_main,
                    structural_stop_price=structural_stop_price,
                    structural_stop_distance_from_scale_pct=stop_distance,
                    structural_stop_risk_usd_fixed_notional=risk_usd,
                    illustrative_round_trip_cost_usd=cost_reserve,
                    structural_stop_loss_usd_with_cost_reserve=(
                        risk_usd + cost_reserve
                    ),
                    notional_for_diagnostic_fixed_risk_usd=notional_for_fixed_risk,
                    margin_at_10x_for_diagnostic_fixed_risk_usd=margin_for_fixed_risk,
                    main_stop_at=main_stop_at,
                    zero_crossings_before_scale=_zero_crossings(
                        path.moves_pct,
                        scale_index,
                    ),
                    seconds_touch_to_launch=(
                        path.timestamps[launch_index]
                        - path.signal.touch_at.timestamp()
                    ),
                    seconds_launch_to_scale=(
                        path.timestamps[scale_index]
                        - path.timestamps[launch_index]
                    ),
                    seconds_touch_to_scale=(
                        path.timestamps[scale_index]
                        - path.signal.touch_at.timestamp()
                    ),
                    complete_horizon=complete,
                    missing_archive_days=";".join(path.missing_archive_days),
                    secondary_exit_reason=exit_reason,
                    secondary_exit_at=(
                        _event_at(path, exit_index)
                        if exit_index is not None
                        else None
                    ),
                    secondary_exit_move_pct=exit_move,
                    secondary_mfe_to_exit_pct=mfe_to_exit,
                    secondary_mae_to_exit_pct=mae_to_exit,
                    secondary_mfe_to_horizon_pct=mfe_to_horizon,
                    secondary_mae_to_horizon_pct=mae_to_horizon,
                    target_hits_json=json.dumps(
                        target_hits,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    protection_results_json=json.dumps(
                        protections,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            )

    return rows


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _row_to_json(row: TriggerEvent) -> str:
    return json.dumps(
        {key: _jsonable(value) for key, value in asdict(row).items()},
        ensure_ascii=False,
        sort_keys=True,
    )


def _row_from_dict(payload: dict[str, Any]) -> TriggerEvent:
    datetime_fields = {
        "touch_at",
        "launch_at",
        "scale_entry_at",
        "main_stop_at",
        "secondary_exit_at",
    }
    values = dict(payload)
    for field in datetime_fields:
        raw = values.get(field)
        values[field] = datetime.fromisoformat(raw).astimezone(UTC) if raw else None
    values["direction"] = cast(Direction, values["direction"])
    values["trigger_status"] = cast(TriggerStatus, values["trigger_status"])
    values["secondary_exit_reason"] = cast(ExitReason | None, values["secondary_exit_reason"])
    return TriggerEvent(**values)


def _load_partial(
    path: Path,
    *,
    expected_per_signal: int,
) -> tuple[list[TriggerEvent], set[tuple[str, str]]]:
    if not path.exists():
        return [], set()
    rows: list[TriggerEvent] = []
    completed_signal_keys: set[tuple[str, str]] = set()
    counts: dict[tuple[str, str], int] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"corrupt resume JSONL at line {line_no}: {path}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"resume row must be JSON object at line {line_no}: {path}")
            row = _row_from_dict(payload)
            rows.append(row)
            key = (row.symbol, row.touch_at.isoformat())
            counts[key] = counts.get(key, 0) + 1
    for key, count in counts.items():
        if count == expected_per_signal:
            completed_signal_keys.add(key)
        else:
            raise ValueError(
                f"partial signal block in resume cache for {key}: "
                f"{count}/{expected_per_signal}"
            )
    return rows, completed_signal_keys


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _distribution(values: list[float]) -> dict[str, float | None]:
    return {
        "p10": _quantile(values, 0.10),
        "p25": _quantile(values, 0.25),
        "median": float(statistics.median(values)) if values else None,
        "p75": _quantile(values, 0.75),
        "p90": _quantile(values, 0.90),
    }


def _target_reached(row: TriggerEvent, target: float) -> bool:
    if row.trigger_status != "triggered":
        return False
    payload = json.loads(row.target_hits_json)
    return payload.get(f"{target:.2f}") is not None


def _scope_symbols(scope: str) -> set[str]:
    if scope == "ALL9":
        return set(ALL_SYMBOLS)
    if scope == "DEV2":
        return set(DEV_SYMBOLS)
    if scope == "HOLDOUT7":
        return set(HOLDOUT_SYMBOLS)
    if scope in ALL_SYMBOLS:
        return {scope}
    raise ValueError(f"unknown scope: {scope}")


def summarize(rows: list[TriggerEvent], scope: str, config: Config) -> list[dict[str, Any]]:
    symbols = _scope_symbols(scope)
    scoped = [row for row in rows if row.symbol in symbols]
    summaries: list[dict[str, Any]] = []
    for min_adverse in config.min_adverse_depths_pct:
        for rebound in config.rebound_confirmations_pct:
            items = [
                row
                for row in scoped
                if math.isclose(row.min_adverse_depth_pct, min_adverse)
                and math.isclose(row.rebound_confirmation_pct, rebound)
            ]
            triggered = [row for row in items if row.trigger_status == "triggered"]
            stops = [row for row in triggered if row.secondary_exit_reason == "structural_stop"]
            stop_distances = [
                row.structural_stop_distance_from_scale_pct
                for row in triggered
                if row.structural_stop_distance_from_scale_pct is not None
            ]
            risks = [
                row.structural_stop_risk_usd_fixed_notional
                for row in triggered
                if row.structural_stop_risk_usd_fixed_notional is not None
            ]
            losses_with_cost = [
                row.structural_stop_loss_usd_with_cost_reserve
                for row in triggered
                if row.structural_stop_loss_usd_with_cost_reserve is not None
            ]
            entry_offsets = [
                row.scale_entry_move_vs_main_pct
                for row in triggered
                if row.scale_entry_move_vs_main_pct is not None
            ]
            record: dict[str, Any] = {
                "scope": scope,
                "min_adverse_depth_pct": min_adverse,
                "rebound_confirmation_pct": rebound,
                "signals": len(items),
                "triggered": len(triggered),
                "trigger_rate_pct": (
                    round(100.0 * len(triggered) / len(items), 6) if items else None
                ),
                "main_stop_before_trigger": sum(
                    row.trigger_status == "main_stop_before_trigger" for row in items
                ),
                "no_trigger": sum(row.trigger_status == "no_trigger" for row in items),
                "data_end": sum(row.trigger_status == "data_end" for row in items),
                "secondary_structural_stops": len(stops),
                "secondary_structural_stop_rate_pct": (
                    round(100.0 * len(stops) / len(triggered), 6) if triggered else None
                ),
                "stop_distance_pct": _distribution([float(value) for value in stop_distances]),
                "risk_usd_at_100_margin_10x": _distribution(
                    [float(value) for value in risks]
                ),
                "stop_loss_usd_with_cost_reserve": _distribution(
                    [float(value) for value in losses_with_cost]
                ),
                "scale_entry_move_vs_main_pct": _distribution(
                    [float(value) for value in entry_offsets]
                ),
            }
            for target in config.targets_pct:
                count = sum(_target_reached(row, target) for row in triggered)
                record[f"reached_plus_{target:.2f}"] = count
                record[f"reached_plus_{target:.2f}_pct"] = (
                    round(100.0 * count / len(triggered), 6) if triggered else None
                )
            for activation in config.protection_activations_pct:
                key = f"activate_{activation:.2f}_floor_{config.positive_floor_pct:.2f}"
                activated = 0
                floor_exits = 0
                structural_before = 0
                for row in triggered:
                    payload = json.loads(row.protection_results_json)[key]
                    activated += bool(payload["activated"])
                    floor_exits += payload["exit_reason"] == "positive_floor"
                    structural_before += (
                        payload["exit_reason"] == "structural_stop_before_activation"
                    )
                record[f"protection_{activation:.2f}_activated"] = activated
                record[f"protection_{activation:.2f}_floor_exits"] = floor_exits
                record[
                    f"protection_{activation:.2f}_structural_before_activation"
                ] = structural_before
                for target in (0.50, 1.00, 2.00, 3.00):
                    reached_before_exit = 0
                    for row in triggered:
                        payload = json.loads(row.protection_results_json)[key]
                        mfe = payload.get("mfe_before_exit_pct")
                        if isinstance(mfe, (int, float)) and float(mfe) >= target:
                            reached_before_exit += 1
                    record[
                        f"protection_{activation:.2f}_reached_plus_{target:.2f}_before_exit"
                    ] = reached_before_exit
                    record[
                        f"protection_{activation:.2f}_reached_plus_{target:.2f}_before_exit_pct"
                    ] = (
                        round(100.0 * reached_before_exit / len(triggered), 6)
                        if triggered
                        else None
                    )
            summaries.append(record)
    return summaries


def _csv_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _write_events_csv(path: Path, rows: list[TriggerEvent]) -> None:
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(TriggerEvent.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in asdict(row).items()})


def _write_records_csv(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for record in records:
        for key in record:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({key: _csv_value(record.get(key)) for key in fieldnames})


def _summary_markdown(summaries: list[dict[str, Any]], contract_hash: str) -> str:
    all9 = [row for row in summaries if row["scope"] == "ALL9"]
    lines = [
        "# SE1 Secondary Entry — Structural Reversal V1",
        "",
        "Research only. Downloads: DISABLED / fail-closed.",
        "",
        (
            "Main Entry structural -1.00% is unchanged. This study creates a NEW "
            "secondary Entry after a causal rebound from the running adverse extreme."
        ),
        (
            "The secondary structural stop is launch/reversal extreme minus 0.10 "
            "percentage points in directional Main-Entry coordinates."
        ),
        "",
        f"Run contract SHA256: `{contract_hash}`",
        "",
        "## ALL9 summary",
        "",
        (
            "| Min adverse | Rebound | Triggered | Trigger % | Structural stop % | "
            "+0.50 % | +1.00 % | +2.00 % | +3.00 % |"
        ),
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in all9:
        lines.append(
            "| {min_adverse_depth_pct:.2f}% | {rebound_confirmation_pct:.2f}% | "
            "{triggered} | {trigger_rate_pct} | {secondary_structural_stop_rate_pct} | "
            "{reached_plus_0.50_pct} | {reached_plus_1.00_pct} | "
            "{reached_plus_2.00_pct} | {reached_plus_3.00_pct} |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Interpretation contract",
            "",
            (
                "This output is anatomy, not an optimized production rule. Do not choose "
                "one row solely because it looks best on ALL9."
            ),
            (
                "Structural stop distance is variable. Leverage does not redefine the stop; "
                "monetary risk follows from notional x stop distance + costs."
            ),
            (
                "Positive-floor variants are diagnostics only and are not production "
                "Exit/Protection rules."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _trade_loader(
    symbol: str,
    heartbeat_seconds: float,
) -> Callable[[Path], TradeDay]:
    def load(path: Path) -> TradeDay:
        return _load_trade_day(
            path,
            progress_label=f"{symbol}/{path.name}",
            heartbeat_seconds=heartbeat_seconds,
            progress_sink=lambda message: print(
                message.replace("[P31 tape]", "[SE1 tape]"),
                flush=True,
            ),
        )

    return load


def run(project_root: Path, output_dir: Path, config: Config) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = discover_sources(project_root)
    signals = load_frozen_signals(sources)
    contract = _run_contract(sources, config)
    contract_path = output_dir / "run_contract.json"
    partial_path = output_dir / "secondary_entry_events.partial.jsonl"

    if contract_path.exists():
        existing = json.loads(contract_path.read_text(encoding="utf-8"))
        if existing != contract:
            raise ValueError("resume contract mismatch; use a fresh output directory")
    else:
        contract_path.write_text(
            json.dumps(contract, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    expected_per_signal = len(config.min_adverse_depths_pct) * len(config.rebound_confirmations_pct)
    existing_rows, completed_keys = _load_partial(
        partial_path,
        expected_per_signal=expected_per_signal,
    )
    rows = list(existing_rows)
    progress = Progress(config.progress_interval_seconds)
    archive_maps = {source.symbol: _archive_map(source.dataset_dir) for source in sources}
    caches = {
        symbol: TradeDayCache(
            max_days=config.day_cache_size,
            loader=_trade_loader(symbol, config.progress_interval_seconds),
        )
        for symbol in ALL_SYMBOLS
    }
    progress.emit("scan", len(completed_keys), len(signals), force=True, detail="resume-aware")
    with partial_path.open("a", encoding="utf-8") as partial:
        processed = len(completed_keys)
        for signal in signals:
            signal_key = (signal.symbol, signal.touch_at.isoformat())
            if signal_key in completed_keys:
                continue
            path = build_path_series(
                signal,
                archive_maps[signal.symbol],
                horizon_hours=config.horizon_hours,
                cache=caches[signal.symbol],
            )
            available_days = sorted(archive_maps[signal.symbol])
            max_day = available_days[-1] if available_days else ""
            internal_missing = [day for day in path.missing_archive_days if day <= max_day]
            if internal_missing:
                raise FileNotFoundError(
                    "internal trade archive gap for "
                    f"{signal.symbol} {signal.touch_at}: {internal_missing}"
                )
            signal_rows = analyze_signal(path, config)
            for row in signal_rows:
                partial.write(_row_to_json(row) + "\n")
                rows.append(row)
            partial.flush()
            processed += 1
            progress.emit(
                "scan",
                processed,
                len(signals),
                detail=f"{signal.symbol} {signal.touch_at.isoformat()}",
            )
    progress.emit("scan", len(signals), len(signals), force=True, detail="complete")

    expected_rows = (
        len(signals)
        * len(config.min_adverse_depths_pct)
        * len(config.rebound_confirmations_pct)
    )
    if len(rows) != expected_rows:
        raise ValueError(f"result row count mismatch: {len(rows)} != {expected_rows}")

    _write_events_csv(output_dir / "secondary_entry_events.csv", rows)
    scopes = ["ALL9", "DEV2", "HOLDOUT7", *ALL_SYMBOLS]
    summaries = [record for scope in scopes for record in summarize(rows, scope, config)]
    _write_records_csv(output_dir / "secondary_entry_summary_by_config.csv", summaries)

    false_confirmations = [
        {key: _csv_value(value) for key, value in asdict(row).items()}
        for row in rows
        if row.trigger_status == "triggered" and row.secondary_exit_reason == "structural_stop"
    ]
    _write_records_csv(output_dir / "secondary_entry_false_confirmations.csv", false_confirmations)

    triggered_rows = [row for row in rows if row.trigger_status == "triggered"]
    risk_records = [
        {
            "symbol": row.symbol,
            "touch_at": row.touch_at.isoformat(),
            "min_adverse_depth_pct": row.min_adverse_depth_pct,
            "rebound_confirmation_pct": row.rebound_confirmation_pct,
            "launch_move_vs_main_pct": row.launch_move_vs_main_pct,
            "scale_entry_move_vs_main_pct": row.scale_entry_move_vs_main_pct,
            "structural_stop_move_vs_main_pct": row.structural_stop_move_vs_main_pct,
            "structural_stop_distance_from_scale_pct": row.structural_stop_distance_from_scale_pct,
            "risk_usd_at_100_margin_10x": row.structural_stop_risk_usd_fixed_notional,
            "illustrative_round_trip_cost_usd": row.illustrative_round_trip_cost_usd,
            "stop_loss_usd_with_cost_reserve": (
                row.structural_stop_loss_usd_with_cost_reserve
            ),
            "notional_for_2usd_gross_risk": row.notional_for_diagnostic_fixed_risk_usd,
            "margin_at_10x_for_2usd_gross_risk": row.margin_at_10x_for_diagnostic_fixed_risk_usd,
        }
        for row in triggered_rows
    ]
    _write_records_csv(output_dir / "secondary_entry_risk_anatomy.csv", risk_records)

    summary_payload = {
        "research": "SE1_SECONDARY_ENTRY_STRUCTURAL_REVERSAL_V1",
        "completed_at": datetime.now(UTC).isoformat(),
        "run_contract_sha256": contract["contract_sha256"],
        "signals": len(signals),
        "event_rows": len(rows),
        "triggered_rows": len(triggered_rows),
        "downloads": "DISABLED",
        "main_entry_changed": False,
        "main_structural_stop_changed": False,
        "live_changed": False,
        "entry_exit_risk_execution_changed": False,
        "illustrative_scale_economics": {
            "margin_usd": config.scale_margin_usd,
            "leverage": config.scale_leverage,
            "notional_usd": config.scale_margin_usd * config.scale_leverage,
            "round_trip_cost_pct_notional": config.illustrative_round_trip_cost_pct,
            "round_trip_cost_usd": (
                config.scale_margin_usd
                * config.scale_leverage
                * config.illustrative_round_trip_cost_pct
                / 100.0
            ),
            "target_net_reward_usd_after_cost_reserve": {
                f"plus_{target:.2f}": (
                    config.scale_margin_usd
                    * config.scale_leverage
                    * target
                    / 100.0
                    - config.scale_margin_usd
                    * config.scale_leverage
                    * config.illustrative_round_trip_cost_pct
                    / 100.0
                )
                for target in config.targets_pct
            },
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(
        _summary_markdown(summaries, str(contract["contract_sha256"])),
        encoding="utf-8",
    )

    provenance = {
        "research_version": "SE1 V1",
        "software_baseline": "bybit-workbench 0.8.5 + accepted additive research patches",
        "period": PERIOD_TAG,
        "symbols": list(ALL_SYMBOLS),
        "entry_signal_count": len(signals),
        "source_fingerprints": contract["sources"],
        "run_contract_sha256": contract["contract_sha256"],
        "config": asdict(config),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"SE1 complete: {output_dir}", flush=True)


def _parse_float_tuple(raw: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("at least one numeric value is required")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SE1 Probe -> Reversal -> Secondary Entry anatomy")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--main-stop-pct", type=float, default=1.00)
    parser.add_argument("--structural-buffer-pct", type=float, default=0.10)
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument(
        "--min-adverse-depths-pct",
        type=_parse_float_tuple,
        default=DEFAULT_MIN_ADVERSE_DEPTHS_PCT,
    )
    parser.add_argument(
        "--rebound-confirmations-pct",
        type=_parse_float_tuple,
        default=DEFAULT_REBOUND_CONFIRMATIONS_PCT,
    )
    parser.add_argument("--scale-margin-usd", type=float, default=100.0)
    parser.add_argument("--scale-leverage", type=float, default=10.0)
    parser.add_argument("--illustrative-round-trip-cost-pct", type=float, default=0.10)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = Config(
        main_stop_pct=args.main_stop_pct,
        structural_buffer_pct=args.structural_buffer_pct,
        horizon_hours=args.horizon_hours,
        min_adverse_depths_pct=args.min_adverse_depths_pct,
        rebound_confirmations_pct=args.rebound_confirmations_pct,
        scale_margin_usd=args.scale_margin_usd,
        scale_leverage=args.scale_leverage,
        illustrative_round_trip_cost_pct=args.illustrative_round_trip_cost_pct,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    run(args.project_root.resolve(), args.output_dir.resolve(), config)


if __name__ == "__main__":
    main()
