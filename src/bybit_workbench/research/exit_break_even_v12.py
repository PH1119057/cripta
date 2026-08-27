from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.flow_reversal_v1 import (
    TradeDay,
    _archive_map,
    _combine_trade_days,
    _load_trade_day,
)
from bybit_workbench.research.mtf_entry import Direction

ExitReason = Literal["initial_stop", "break_even", "horizon", "data_end"]

DEFAULT_ACTIVATION_R = (0.25, 0.35, 0.50, 0.75, 1.00)
DEFAULT_BE_BUFFER_BPS = (0.0, 5.0, 10.0, 15.0, 20.0)
DEFAULT_HORIZON_HOURS = (6, 12, 24, 48, 72)
DEFAULT_RUNNER_TARGETS_PCT = (2.0, 3.0, 5.0, 10.0, 20.0)


@dataclass(frozen=True, slots=True)
class CoreSignal:
    symbol: str
    direction: Direction
    touch_at: datetime
    entry_price: float
    source_row: dict[str, str]


@dataclass(frozen=True, slots=True)
class SignalSource:
    symbol: str
    p40_dir: Path
    features_path: Path
    summary_path: Path
    dataset_dir: Path


@dataclass(frozen=True, slots=True)
class PathSeries:
    signal: CoreSignal
    timestamps: tuple[float, ...]
    moves_pct: tuple[float, ...]
    available_until: datetime

    @property
    def starts_at(self) -> datetime:
        return self.signal.touch_at


@dataclass(frozen=True, slots=True)
class PolicyResult:
    activation_r: float
    activation_pct: float
    be_buffer_bps: float
    be_floor_pct: float
    exit_reason: ExitReason
    exit_at: datetime
    exit_move_pct: float
    activated_at: datetime | None
    initial_stop_hit: bool
    be_hit: bool
    completed_horizon: bool


@dataclass(frozen=True, slots=True)
class BaselinePathMetrics:
    symbol: str
    direction: Direction
    touch_at: datetime
    entry_price: float
    complete_6h: bool
    complete_12h: bool
    complete_24h: bool
    complete_48h: bool
    complete_72h: bool
    initial_stop_seconds: float | None
    mfe_6h_pct: float | None
    mfe_12h_pct: float | None
    mfe_24h_pct: float | None
    mfe_48h_pct: float | None
    mfe_72h_pct: float | None
    mae_6h_pct: float | None
    mae_12h_pct: float | None
    mae_24h_pct: float | None
    mae_48h_pct: float | None
    mae_72h_pct: float | None
    time_to_0_25_pct_seconds: float | None
    time_to_0_35_pct_seconds: float | None
    time_to_0_50_pct_seconds: float | None
    time_to_0_75_pct_seconds: float | None
    time_to_1_00_pct_seconds: float | None
    time_to_2_00_pct_seconds: float | None
    time_to_3_00_pct_seconds: float | None
    time_to_5_00_pct_seconds: float | None
    time_to_10_00_pct_seconds: float | None
    time_to_20_00_pct_seconds: float | None


@dataclass(frozen=True, slots=True)
class ExitResearchConfig:
    initial_stop_pct: float = 1.0
    horizon_hours: int = 72
    activation_r_values: tuple[float, ...] = DEFAULT_ACTIVATION_R
    be_buffer_bps_values: tuple[float, ...] = DEFAULT_BE_BUFFER_BPS
    runner_targets_pct: tuple[float, ...] = DEFAULT_RUNNER_TARGETS_PCT

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if any(value <= 0 for value in self.activation_r_values):
            raise ValueError("activation_r_values must be positive")
        if any(value < 0 for value in self.be_buffer_bps_values):
            raise ValueError("be_buffer_bps_values cannot be negative")
        if any(value <= 0 for value in self.runner_targets_pct):
            raise ValueError("runner_targets_pct must be positive")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return cast(dict[str, Any], payload)


def discover_source(p40_dir: Path) -> SignalSource:
    p40_dir = p40_dir.resolve()
    features_path = p40_dir / "absorption_features.csv"
    summary_path = p40_dir / "summary.json"
    if not features_path.exists():
        raise FileNotFoundError(f"P40 absorption features not found: {features_path}")
    if not summary_path.exists():
        raise FileNotFoundError(f"P40 summary not found: {summary_path}")
    summary = _read_json(summary_path)
    dataset_raw = summary.get("dataset_dir")
    if not isinstance(dataset_raw, str) or not dataset_raw.strip():
        raise ValueError(f"P40 summary has no dataset_dir: {summary_path}")
    dataset_dir = Path(dataset_raw)
    if not dataset_dir.exists():
        raise FileNotFoundError(f"P40 dataset directory not found: {dataset_dir}")
    symbol_raw = summary.get("symbol")
    if not isinstance(symbol_raw, str) or not symbol_raw.strip():
        with features_path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            first = next(reader, None)
        if first is None or not first.get("symbol"):
            raise ValueError(f"cannot infer symbol from {features_path}")
        symbol_raw = str(first["symbol"])
    return SignalSource(
        symbol=symbol_raw,
        p40_dir=p40_dir,
        features_path=features_path,
        summary_path=summary_path,
        dataset_dir=dataset_dir,
    )


def load_core_signals(source: SignalSource) -> tuple[CoreSignal, ...]:
    items: list[CoreSignal] = []
    with source.features_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            direction_raw = str(row.get("direction") or "")
            if direction_raw not in {"Long", "Short"}:
                raise ValueError(
                    f"unsupported direction in {source.features_path}: {direction_raw}"
                )
            touch_raw = str(row.get("touch_at") or "")
            entry_raw = str(row.get("entry_price") or "")
            if not touch_raw or not entry_raw:
                continue
            items.append(
                CoreSignal(
                    symbol=str(row.get("symbol") or source.symbol),
                    direction=cast(Direction, direction_raw),
                    touch_at=datetime.fromisoformat(touch_raw).astimezone(UTC),
                    entry_price=float(entry_raw),
                    source_row={str(key): str(value or "") for key, value in row.items()},
                )
            )
    return tuple(items)


def directional_move_pct(direction: Direction, entry_price: float, price: float) -> float:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    raw = (price - entry_price) / entry_price * 100.0
    return raw if direction == "Long" else -raw


def _combine_days(paths: tuple[Path, ...]) -> TradeDay:
    combined: TradeDay | None = None
    for path in paths:
        day = _load_trade_day(path)
        combined = day if combined is None else _combine_trade_days(combined, day)
    if combined is None:
        return TradeDay((), ())
    return combined


def _dates_for_horizon(start: datetime, hours: int) -> tuple[str, ...]:
    end = start + timedelta(hours=hours)
    current = start.date()
    last = end.date()
    items: list[str] = []
    while current <= last:
        items.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(items)


def build_path_series(
    signal: CoreSignal,
    archive_by_day: dict[str, Path],
    *,
    horizon_hours: int,
) -> PathSeries:
    required_days = _dates_for_horizon(signal.touch_at, horizon_hours)
    existing = tuple(archive_by_day[day] for day in required_days if day in archive_by_day)
    if not existing:
        return PathSeries(signal, (), (), signal.touch_at)
    tape = _combine_days(existing)
    start_ts = signal.touch_at.timestamp()
    requested_end = signal.touch_at + timedelta(hours=horizon_hours)
    end_ts = requested_end.timestamp()
    start_index = bisect.bisect_left(tape.timestamps, start_ts)
    end_index = bisect.bisect_right(tape.timestamps, end_ts)
    timestamps = tape.timestamps[start_index:end_index]
    moves = tuple(
        directional_move_pct(signal.direction, signal.entry_price, price)
        for price in tape.prices[start_index:end_index]
    )
    if not timestamps:
        return PathSeries(signal, (), (), signal.touch_at)
    available_until = datetime.fromtimestamp(timestamps[-1], UTC)
    return PathSeries(signal, timestamps, moves, available_until)


def first_hit_seconds(path: PathSeries, threshold_pct: float, *, favorable: bool) -> float | None:
    start_ts = path.signal.touch_at.timestamp()
    for timestamp, move in zip(path.timestamps, path.moves_pct, strict=True):
        if favorable and move >= threshold_pct:
            return timestamp - start_ts
        if not favorable and move <= -threshold_pct:
            return timestamp - start_ts
    return None


def _window_extreme(
    path: PathSeries,
    hours: int,
    *,
    maximum: bool,
) -> float | None:
    end_ts = (path.signal.touch_at + timedelta(hours=hours)).timestamp()
    values = [
        move
        for timestamp, move in zip(path.timestamps, path.moves_pct, strict=True)
        if timestamp <= end_ts
    ]
    if not values:
        return None
    return max(values) if maximum else min(values)


def _is_horizon_complete(path: PathSeries, hours: int) -> bool:
    required_until = path.signal.touch_at + timedelta(hours=hours)
    # Daily archives normally stop a fraction of a second before midnight. A small
    # tolerance avoids falsely marking a complete window as censored.
    return path.available_until >= required_until - timedelta(seconds=2)


def baseline_metrics(path: PathSeries, *, initial_stop_pct: float) -> BaselinePathMetrics:
    def hit(value: float) -> float | None:
        return first_hit_seconds(path, value, favorable=True)

    return BaselinePathMetrics(
        symbol=path.signal.symbol,
        direction=path.signal.direction,
        touch_at=path.signal.touch_at,
        entry_price=path.signal.entry_price,
        complete_6h=_is_horizon_complete(path, 6),
        complete_12h=_is_horizon_complete(path, 12),
        complete_24h=_is_horizon_complete(path, 24),
        complete_48h=_is_horizon_complete(path, 48),
        complete_72h=_is_horizon_complete(path, 72),
        initial_stop_seconds=first_hit_seconds(path, initial_stop_pct, favorable=False),
        mfe_6h_pct=_window_extreme(path, 6, maximum=True),
        mfe_12h_pct=_window_extreme(path, 12, maximum=True),
        mfe_24h_pct=_window_extreme(path, 24, maximum=True),
        mfe_48h_pct=_window_extreme(path, 48, maximum=True),
        mfe_72h_pct=_window_extreme(path, 72, maximum=True),
        mae_6h_pct=_window_extreme(path, 6, maximum=False),
        mae_12h_pct=_window_extreme(path, 12, maximum=False),
        mae_24h_pct=_window_extreme(path, 24, maximum=False),
        mae_48h_pct=_window_extreme(path, 48, maximum=False),
        mae_72h_pct=_window_extreme(path, 72, maximum=False),
        time_to_0_25_pct_seconds=hit(0.25),
        time_to_0_35_pct_seconds=hit(0.35),
        time_to_0_50_pct_seconds=hit(0.50),
        time_to_0_75_pct_seconds=hit(0.75),
        time_to_1_00_pct_seconds=hit(1.00),
        time_to_2_00_pct_seconds=hit(2.00),
        time_to_3_00_pct_seconds=hit(3.00),
        time_to_5_00_pct_seconds=hit(5.00),
        time_to_10_00_pct_seconds=hit(10.00),
        time_to_20_00_pct_seconds=hit(20.00),
    )


def simulate_be_policy(
    path: PathSeries,
    *,
    initial_stop_pct: float,
    activation_r: float,
    be_buffer_bps: float,
    horizon_hours: int,
) -> PolicyResult:
    activation_pct = initial_stop_pct * activation_r
    be_floor_pct = be_buffer_bps / 100.0
    if be_floor_pct >= activation_pct:
        raise ValueError("break-even floor must be below activation threshold")
    horizon_at = path.signal.touch_at + timedelta(hours=horizon_hours)
    horizon_ts = horizon_at.timestamp()
    activated_at: datetime | None = None
    activated = False
    for timestamp, move in zip(path.timestamps, path.moves_pct, strict=True):
        if timestamp > horizon_ts:
            break
        event_at = datetime.fromtimestamp(timestamp, UTC)
        if not activated:
            if move <= -initial_stop_pct:
                return PolicyResult(
                    activation_r=activation_r,
                    activation_pct=activation_pct,
                    be_buffer_bps=be_buffer_bps,
                    be_floor_pct=be_floor_pct,
                    exit_reason="initial_stop",
                    exit_at=event_at,
                    exit_move_pct=-initial_stop_pct,
                    activated_at=None,
                    initial_stop_hit=True,
                    be_hit=False,
                    completed_horizon=True,
                )
            if move >= activation_pct:
                activated = True
                activated_at = event_at
                continue
        elif move <= be_floor_pct:
            return PolicyResult(
                activation_r=activation_r,
                activation_pct=activation_pct,
                be_buffer_bps=be_buffer_bps,
                be_floor_pct=be_floor_pct,
                exit_reason="break_even",
                exit_at=event_at,
                exit_move_pct=be_floor_pct,
                activated_at=activated_at,
                initial_stop_hit=False,
                be_hit=True,
                completed_horizon=True,
            )
    if _is_horizon_complete(path, horizon_hours):
        terminal = 0.0
        if path.moves_pct:
            index = bisect.bisect_right(path.timestamps, horizon_ts) - 1
            if index >= 0:
                terminal = path.moves_pct[index]
        return PolicyResult(
            activation_r=activation_r,
            activation_pct=activation_pct,
            be_buffer_bps=be_buffer_bps,
            be_floor_pct=be_floor_pct,
            exit_reason="horizon",
            exit_at=horizon_at,
            exit_move_pct=terminal,
            activated_at=activated_at,
            initial_stop_hit=False,
            be_hit=False,
            completed_horizon=True,
        )
    exit_at = path.available_until
    terminal = path.moves_pct[-1] if path.moves_pct else 0.0
    return PolicyResult(
        activation_r=activation_r,
        activation_pct=activation_pct,
        be_buffer_bps=be_buffer_bps,
        be_floor_pct=be_floor_pct,
        exit_reason="data_end",
        exit_at=exit_at,
        exit_move_pct=terminal,
        activated_at=activated_at,
        initial_stop_hit=False,
        be_hit=False,
        completed_horizon=False,
    )


def target_before_stop(path: PathSeries, target_pct: float, initial_stop_pct: float) -> bool | None:
    favorable = first_hit_seconds(path, target_pct, favorable=True)
    adverse = first_hit_seconds(path, initial_stop_pct, favorable=False)
    if favorable is not None and (adverse is None or favorable <= adverse):
        return True
    if adverse is not None and (favorable is None or adverse < favorable):
        return False
    return None


def target_before_policy_exit(
    path: PathSeries,
    target_pct: float,
    result: PolicyResult,
) -> bool | None:
    target_seconds = first_hit_seconds(path, target_pct, favorable=True)
    if target_seconds is None:
        return False if result.completed_horizon else None
    target_at = path.signal.touch_at + timedelta(seconds=target_seconds)
    return target_at <= result.exit_at


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _median(values: list[float]) -> float | None:
    return None if not values else round(float(statistics.median(values)), 6)


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 6)


def summarise_policy(
    paths: tuple[PathSeries, ...],
    *,
    initial_stop_pct: float,
    activation_r: float,
    be_buffer_bps: float,
    horizon_hours: int,
    runner_targets_pct: tuple[float, ...],
) -> dict[str, Any]:
    results = tuple(
        simulate_be_policy(
            path,
            initial_stop_pct=initial_stop_pct,
            activation_r=activation_r,
            be_buffer_bps=be_buffer_bps,
            horizon_hours=horizon_hours,
        )
        for path in paths
    )
    complete = tuple(item for item in results if item.completed_horizon)
    activated = tuple(item for item in results if item.activated_at is not None)
    initial_stop = sum(item.exit_reason == "initial_stop" for item in results)
    be_exits = sum(item.exit_reason == "break_even" for item in results)
    horizon_exits = sum(item.exit_reason == "horizon" for item in results)
    row: dict[str, Any] = {
        "activation_r": activation_r,
        "activation_pct": round(initial_stop_pct * activation_r, 6),
        "be_buffer_bps": be_buffer_bps,
        "be_floor_pct": round(be_buffer_bps / 100.0, 6),
        "signals": len(paths),
        "complete_or_closed": len(complete),
        "censored": len(paths) - len(complete),
        "activated": len(activated),
        "activated_percent": _percent(len(activated), len(paths)),
        "initial_stop_exits": initial_stop,
        "break_even_exits": be_exits,
        "horizon_exits": horizon_exits,
        "break_even_exit_percent_of_activated": _percent(be_exits, len(activated)),
        "median_exit_move_pct_complete": _median(
            [item.exit_move_pct for item in complete]
        ),
    }
    for target in runner_targets_pct:
        baseline_candidates = 0
        baseline_reached = 0
        preserved = 0
        killed = 0
        for path, result in zip(paths, results, strict=True):
            baseline = target_before_stop(path, target, initial_stop_pct)
            if baseline is None:
                continue
            baseline_candidates += 1
            if not baseline:
                continue
            baseline_reached += 1
            with_policy = target_before_policy_exit(path, target, result)
            if with_policy is True:
                preserved += 1
            elif with_policy is False:
                killed += 1
        key = str(target).replace(".", "_")
        row[f"baseline_target_{key}_pct_reached"] = baseline_reached
        row[f"policy_target_{key}_pct_preserved"] = preserved
        row[f"policy_target_{key}_pct_killed"] = killed
        row[f"runner_preservation_{key}_pct"] = _percent(preserved, baseline_reached)
        row[f"runner_kill_{key}_pct"] = _percent(killed, baseline_reached)
        row[f"target_{key}_baseline_decisive"] = baseline_candidates
    return row


def summarise_baseline(metrics: tuple[BaselinePathMetrics, ...]) -> dict[str, Any]:
    summary: dict[str, Any] = {"signals": len(metrics)}
    for hours in DEFAULT_HORIZON_HOURS:
        complete = [item for item in metrics if bool(getattr(item, f"complete_{hours}h"))]
        mfe = [
            float(value)
            for item in complete
            if (value := getattr(item, f"mfe_{hours}h_pct")) is not None
        ]
        mae = [
            float(value)
            for item in complete
            if (value := getattr(item, f"mae_{hours}h_pct")) is not None
        ]
        summary[f"horizon_{hours}h"] = {
            "complete_signals": len(complete),
            "mfe_pct": {
                "median": _median(mfe),
                "p75": _quantile(mfe, 0.75),
                "p90": _quantile(mfe, 0.90),
                "p95": _quantile(mfe, 0.95),
            },
            "mae_pct": {
                "median": _median(mae),
                "p25": _quantile(mae, 0.25),
                "p10": _quantile(mae, 0.10),
                "p05": _quantile(mae, 0.05),
            },
        }
    return summary


def _baseline_row(metric: BaselinePathMetrics) -> dict[str, Any]:
    return asdict(metric)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )


def _format_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}%"


def _write_summary_md(
    path: Path,
    *,
    config: ExitResearchConfig,
    sources: tuple[SignalSource, ...],
    baseline_by_symbol: dict[str, dict[str, Any]],
    policy_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# Exit Research V1 — break-even timing / runner preservation",
        "",
        "Research-only. Entry V1 is frozen. This report changes no live trading logic.",
        "",
        (
            "Initial structural stop used in this first pass: "
            f"**{config.initial_stop_pct:.3f}% price**."
        ),
        (
            f"Maximum path horizon: **{config.horizon_hours}h**; "
            "late-window signals may be right-censored."
        ),
        (
            "Economic break-even is represented as a configurable favorable buffer in bps; "
            "it is not yet claimed to be the account's exact fee/slippage cost."
        ),
        "",
        "## Sources",
        "",
    ]
    for source in sources:
        lines.append(f"- {source.symbol}: `{source.p40_dir}`")
    lines.extend(["", "## Baseline path shape", ""])
    for symbol, summary in baseline_by_symbol.items():
        lines.append(f"### {symbol}")
        lines.append("")
        for hours in DEFAULT_HORIZON_HOURS:
            horizon = cast(dict[str, Any], summary[f"horizon_{hours}h"])
            mfe = cast(dict[str, Any], horizon["mfe_pct"])
            lines.append(
                f"- {hours}h complete N={horizon['complete_signals']}: "
                f"MFE median {_format_pct(mfe['median'])}, "
                f"p90 {_format_pct(mfe['p90'])}, p95 {_format_pct(mfe['p95'])}."
            )
        lines.append("")
    lines.extend(
        [
            "## Break-even grid",
            "",
            (
                "`runner_preservation_5_0_pct` answers the main question: among signals "
                "that could reach +5% before the original -1% stop, what share still "
                "reach +5% after this break-even policy?"
            ),
            "",
            (
                "| Activation | BE buffer | Armed | BE exits | Preserve +2% | "
                "Preserve +5% | Preserve +10% |"
            ),
            "|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in policy_rows:
        lines.append(
            "| "
            f"{float(row['activation_r']):.2f}R "
            f"| {float(row['be_buffer_bps']):.1f} bps "
            f"| {row['activated']} "
            f"| {row['break_even_exits']} "
            f"| {_format_pct(row.get('runner_preservation_2_0_pct'))} "
            f"| {_format_pct(row.get('runner_preservation_5_0_pct'))} "
            f"| {_format_pct(row.get('runner_preservation_10_0_pct'))} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- This pass does **not** choose a final break-even rule automatically.",
            (
                "- It does **not** implement trailing stop, partial TP, leverage, "
                "position sizing, or portfolio risk."
            ),
            (
                "- A policy that removes losses but kills rare +5…+20% runners is "
                "considered expensive for this strategy."
            ),
            (
                "- After a policy is selected on UNI+LINK, it should be frozen and "
                "replayed without retuning on the remaining panel."
            ),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_research(
    sources: tuple[SignalSource, ...],
    *,
    output_dir: Path,
    config: ExitResearchConfig,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_paths: list[PathSeries] = []
    baseline_rows: list[dict[str, Any]] = []
    baseline_by_symbol: dict[str, dict[str, Any]] = {}

    for source in sources:
        signals = load_core_signals(source)
        archive_by_day = _archive_map(source.dataset_dir)
        symbol_paths = tuple(
            build_path_series(signal, archive_by_day, horizon_hours=config.horizon_hours)
            for signal in signals
        )
        all_paths.extend(symbol_paths)
        metrics = tuple(
            baseline_metrics(path, initial_stop_pct=config.initial_stop_pct)
            for path in symbol_paths
        )
        baseline_by_symbol[source.symbol] = summarise_baseline(metrics)
        baseline_rows.extend(_baseline_row(metric) for metric in metrics)

    combined_paths = tuple(all_paths)
    policy_rows: list[dict[str, Any]] = []
    for activation_r in config.activation_r_values:
        for be_buffer_bps in config.be_buffer_bps_values:
            if be_buffer_bps / 100.0 >= config.initial_stop_pct * activation_r:
                continue
            row = summarise_policy(
                combined_paths,
                initial_stop_pct=config.initial_stop_pct,
                activation_r=activation_r,
                be_buffer_bps=be_buffer_bps,
                horizon_hours=config.horizon_hours,
                runner_targets_pct=config.runner_targets_pct,
            )
            row["scope"] = "POOLED_UNI_LINK"
            policy_rows.append(row)

    _write_csv(output_dir / "baseline_paths.csv", baseline_rows)
    _write_csv(output_dir / "break_even_policy_grid.csv", policy_rows)
    summary = {
        "architecture": "p45_exit_break_even_v1",
        "research_only": True,
        "entry_frozen": True,
        "initial_stop_pct": config.initial_stop_pct,
        "horizon_hours": config.horizon_hours,
        "activation_r_values": config.activation_r_values,
        "be_buffer_bps_values": config.be_buffer_bps_values,
        "runner_targets_pct": config.runner_targets_pct,
        "sources": [
            {
                "symbol": source.symbol,
                "p40_dir": str(source.p40_dir),
                "dataset_dir": str(source.dataset_dir),
            }
            for source in sources
        ],
        "baseline_by_symbol": baseline_by_symbol,
        "policy_grid_rows": len(policy_rows),
        "notes": [
            "Break-even buffers are research scenarios, not claimed account-specific fee values.",
            (
                "Runner preservation is measured against the same original -1% "
                "structural-stop baseline."
            ),
            (
                "Late signals without enough future archives are right-censored instead "
                "of treated as failures."
            ),
            "No trailing, partial TP, leverage, sizing, or live execution logic is changed by P45.",
        ],
    }
    _write_json(output_dir / "summary.json", summary)
    _write_summary_md(
        output_dir / "summary.md",
        config=config,
        sources=sources,
        baseline_by_symbol=baseline_by_symbol,
        policy_rows=policy_rows,
    )
    return summary


def _parse_float_tuple(raw: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("at least one numeric value is required")
    return values


def _default_output_dir(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return root / "reports" / "exit_research_v1" / f"UNI_LINK_{stamp}"


def _resolve_latest_uni_p40(root: Path) -> Path:
    base = root / "reports" / "entry_research_v13"
    candidates = sorted(base.glob("UNIUSDT_*"), key=lambda item: item.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"UNI P40 directory not found under {base}")
    return candidates[-1]


def _resolve_link_p40(root: Path) -> Path:
    path = root / "reports" / "cross_asset_validation" / "LINKUSDT_20260518_20260816" / "p40"
    if not path.exists():
        raise FileNotFoundError(f"LINK P40 directory not found: {path}")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="P45 Exit Research V1: break-even timing")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--uni-p40-dir", type=Path)
    parser.add_argument("--link-p40-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--initial-stop-pct", type=float, default=1.0)
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument(
        "--activation-r",
        default=",".join(str(value) for value in DEFAULT_ACTIVATION_R),
    )
    parser.add_argument(
        "--be-buffer-bps",
        default=",".join(str(value) for value in DEFAULT_BE_BUFFER_BPS),
    )
    args = parser.parse_args()

    root = args.root.resolve()
    uni_dir = args.uni_p40_dir or _resolve_latest_uni_p40(root)
    link_dir = args.link_p40_dir or _resolve_link_p40(root)
    output_dir = args.output_dir or _default_output_dir(root)
    config = ExitResearchConfig(
        initial_stop_pct=args.initial_stop_pct,
        horizon_hours=args.horizon_hours,
        activation_r_values=_parse_float_tuple(args.activation_r),
        be_buffer_bps_values=_parse_float_tuple(args.be_buffer_bps),
    )
    sources = (discover_source(uni_dir), discover_source(link_dir))
    summary = run_research(sources, output_dir=output_dir, config=config)
    print(f"P45 sources: {', '.join(source.symbol for source in sources)}")
    total_signals = sum(
        int(item["signals"]) for item in summary["baseline_by_symbol"].values()
    )
    print(f"P45 signals: {total_signals}")
    print(f"P45 policy grid rows: {summary['policy_grid_rows']}")
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
