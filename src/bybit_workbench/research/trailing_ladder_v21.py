from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bybit_workbench.research.exit_architecture_oos_v19 import (
    ArchitectureConfig,
    simulate_three_architectures,
)
from bybit_workbench.research.exit_break_even_v13 import (
    PathSeries,
    SignalSource,
    TradeDayCache,
    build_path_series,
    discover_source,
    load_core_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map

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
EXPECTED_DEV2 = 227
EXPECTED_HOLDOUT7 = 836

LADDER_POLICY_IDS = (
    "LADDER_PRE0P10_POST0P20",
    "LADDER_PRE0P10_POST0P25",
    "LADDER_PRE0P10_POST0P30",
)
CONTROL_POLICY_IDS = (
    "A_SIMPLE_TAKE_1P00",
    "B_FULL_RUNNER_MFE_GB1P50",
)
ALL_POLICY_IDS = CONTROL_POLICY_IDS + LADDER_POLICY_IDS


@dataclass(frozen=True, slots=True)
class LadderConfig:
    initial_stop_pct: float = 1.0
    activation_pct: float = 0.10
    pre_one_step_pct: float = 0.10
    switch_level_pct: float = 1.00
    post_one_steps_pct: tuple[float, ...] = (0.20, 0.25, 0.30)
    horizon_hours: int = 72
    day_cache_size: int = 6
    progress_interval_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if self.activation_pct <= 0:
            raise ValueError("activation_pct must be positive")
        if self.pre_one_step_pct <= 0:
            raise ValueError("pre_one_step_pct must be positive")
        if self.switch_level_pct <= self.activation_pct:
            raise ValueError("switch_level_pct must be above activation_pct")
        if not self.post_one_steps_pct:
            raise ValueError("post_one_steps_pct must not be empty")
        if any(step <= 0 for step in self.post_one_steps_pct):
            raise ValueError("post-one steps must be positive")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class PolicyResult:
    symbol: str
    touch_at: str
    policy_id: str
    exit_reason: str
    exit_at: str
    exit_move_pct: float
    max_favorable_pct: float
    completed_horizon: bool
    activated: bool
    reached_one_pct: bool
    reached_two_pct: bool
    reached_three_pct: bool
    reached_five_pct: bool
    reached_ten_pct: bool


class ProgressReporter:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(
        self,
        *,
        processed: int,
        total: int,
        force: bool = False,
        detail: str = "",
    ) -> None:
        now = time.monotonic()
        if not force and now - self.last_emit < self.interval_seconds:
            return
        elapsed = max(0.0, now - self.started)
        eta: float | None = None
        if processed > 0 and processed < total:
            eta = elapsed / processed * (total - processed)
        eta_text = "n/a" if eta is None else _format_duration(eta)
        suffix = f" | {detail}" if detail else ""
        print(
            f"[P47H] processed={processed}/{total} "
            f"elapsed={_format_duration(elapsed)} ETA={eta_text}{suffix}",
            flush=True,
        )
        self.last_emit = now


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def validation_p40(root: Path, symbol: str) -> Path:
    return root / "reports" / "cross_asset_validation" / f"{symbol}_{PERIOD_TAG}" / "p40"


def discover_sources(root: Path) -> tuple[SignalSource, ...]:
    sources: list[SignalSource] = []
    for symbol in ALL_SYMBOLS:
        source = discover_source(validation_p40(root, symbol))
        if source.symbol != symbol:
            raise ValueError(f"P40 symbol mismatch: expected {symbol}, got {source.symbol}")
        sources.append(source)
    return tuple(sources)


def _ladder_stop_from_mfe(mfe_pct: float, post_step_pct: float, config: LadderConfig) -> float:
    """Return a monotonic staircase stop implied by current MFE.

    Frozen interpretation of the user's proposal:
    +0.10 -> 0.00, +0.20 -> +0.10, ... +1.00 -> +0.90.
    Above +1.00, the staircase spacing is post_step_pct, but the stop is never loosened.
    """
    eps = 1e-12
    if mfe_pct < config.activation_pct - eps:
        return -config.initial_stop_pct

    pre_steps = math.floor((min(mfe_pct, config.switch_level_pct) + eps) / config.pre_one_step_pct)
    pre_stop = max(0.0, (pre_steps - 1) * config.pre_one_step_pct)
    if mfe_pct <= config.switch_level_pct + eps:
        return pre_stop

    post_steps = math.floor((mfe_pct + eps) / post_step_pct)
    post_stop = max(0.0, (post_steps - 1) * post_step_pct)
    return max(pre_stop, post_stop)


def simulate_ladder(
    path: PathSeries,
    post_step_pct: float,
    config: LadderConfig,
) -> PolicyResult:
    if not path.moves_pct:
        raise ValueError(f"empty path for {path.signal.symbol} {path.signal.touch_at.isoformat()}")

    mfe = path.moves_pct[0]
    stop = -config.initial_stop_pct
    activated = False
    exit_index: int | None = None
    exit_reason = "data_end"
    exit_move = path.moves_pct[-1]

    for index, move in enumerate(path.moves_pct):
        mfe = max(mfe, move)
        if not activated:
            if move <= -config.initial_stop_pct:
                exit_index = index
                exit_reason = "initial_stop"
                exit_move = -config.initial_stop_pct
                break
            if mfe >= config.activation_pct:
                activated = True
                stop = max(stop, _ladder_stop_from_mfe(mfe, post_step_pct, config))
            continue

        stop = max(stop, _ladder_stop_from_mfe(mfe, post_step_pct, config))
        if move <= stop:
            exit_index = index
            exit_reason = "trailing_stop"
            exit_move = stop
            break

    required_until = path.signal.touch_at.timestamp() + config.horizon_hours * 3600
    completed = path.complete_through.timestamp() >= required_until
    if exit_index is None:
        if completed:
            exit_reason = "horizon"
        exit_index = len(path.moves_pct) - 1
        exit_move = path.moves_pct[exit_index]

    exit_at = datetime.fromtimestamp(path.timestamps[exit_index], UTC).isoformat()
    policy_id = f"LADDER_PRE0P10_POST{post_step_pct:.2f}".replace(".", "P")
    return PolicyResult(
        symbol=path.signal.symbol,
        touch_at=path.signal.touch_at.isoformat(),
        policy_id=policy_id,
        exit_reason=exit_reason,
        exit_at=exit_at,
        exit_move_pct=round(float(exit_move), 9),
        max_favorable_pct=round(float(max(path.moves_pct)), 9),
        completed_horizon=completed,
        activated=activated,
        reached_one_pct=max(path.moves_pct) >= 1.0,
        reached_two_pct=max(path.moves_pct) >= 2.0,
        reached_three_pct=max(path.moves_pct) >= 3.0,
        reached_five_pct=max(path.moves_pct) >= 5.0,
        reached_ten_pct=max(path.moves_pct) >= 10.0,
    )


def _normalise_control(result: Any) -> PolicyResult:
    mfe = float(result.max_favorable_pct)
    return PolicyResult(
        symbol=result.symbol,
        touch_at=result.touch_at.isoformat(),
        policy_id=result.policy_id,
        exit_reason=result.exit_reason,
        exit_at=result.exit_at.isoformat(),
        exit_move_pct=round(float(result.exit_move_pct), 9),
        max_favorable_pct=round(mfe, 9),
        completed_horizon=bool(result.completed_horizon),
        activated=bool(result.early_activated),
        reached_one_pct=mfe >= 1.0,
        reached_two_pct=mfe >= 2.0,
        reached_three_pct=mfe >= 3.0,
        reached_five_pct=mfe >= 5.0,
        reached_ten_pct=mfe >= 10.0,
    )


def simulate_policies(path: PathSeries, config: LadderConfig) -> tuple[PolicyResult, ...]:
    architecture_config = ArchitectureConfig(
        initial_stop_pct=config.initial_stop_pct,
        early_activation_pct=config.activation_pct,
        horizon_hours=config.horizon_hours,
        day_cache_size=config.day_cache_size,
        progress_interval_seconds=config.progress_interval_seconds,
    )
    simple, full_runner, _split = simulate_three_architectures(path, architecture_config)
    results: list[PolicyResult] = [
        _normalise_control(simple),
        _normalise_control(full_runner),
    ]
    results.extend(
        simulate_ladder(path, post_step, config)
        for post_step in config.post_one_steps_pct
    )
    return tuple(results)


def _profit_factor(values: list[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses <= 1e-12:
        return None
    return round(gains / losses, 6)


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return round(max_dd, 6)


def _median(values: list[float]) -> float | None:
    return round(float(statistics.median(values)), 6) if values else None


def _scope_for_symbol(symbol: str) -> str:
    return "DEV2" if symbol in DEV_SYMBOLS else "HOLDOUT7"


def summarize_scope(
    results: list[PolicyResult],
    *,
    scope: str,
    policy_id: str,
    sample_span_days: float,
) -> dict[str, Any]:
    if scope == "ALL9":
        scoped = [item for item in results if item.policy_id == policy_id]
    elif scope == "DEV2":
        scoped = [
            item
            for item in results
            if item.policy_id == policy_id and item.symbol in DEV_SYMBOLS
        ]
    elif scope == "HOLDOUT7":
        scoped = [
            item
            for item in results
            if item.policy_id == policy_id and item.symbol in HOLDOUT_SYMBOLS
        ]
    else:
        scoped = [
            item
            for item in results
            if item.policy_id == policy_id and item.symbol == scope
        ]

    values = [item.exit_move_pct for item in scoped]
    gross = sum(values)
    monthly = gross / sample_span_days * 30.0 if sample_span_days > 0 else None
    return {
        "scope": scope,
        "policy_id": policy_id,
        "signals": len(scoped),
        "gross_sum_pct": round(gross, 6),
        "mean_trade_pct": round(gross / len(scoped), 6) if scoped else None,
        "median_trade_pct": _median(values),
        "profit_factor": _profit_factor(values),
        "max_drawdown_pct": _max_drawdown(values),
        "initial_stop_exits": sum(item.exit_reason == "initial_stop" for item in scoped),
        "trailing_stop_exits": sum(item.exit_reason == "trailing_stop" for item in scoped),
        "horizon_exits": sum(item.exit_reason == "horizon" for item in scoped),
        "data_end_exits": sum(item.exit_reason == "data_end" for item in scoped),
        "positive": sum(value > 1e-12 for value in values),
        "negative": sum(value < -1e-12 for value in values),
        "scratch": sum(abs(value) <= 1e-12 for value in values),
        "fixed_notional_30d_equivalent_pct": round(monthly, 6) if monthly is not None else None,
        "mfe_ge_1": sum(item.reached_one_pct for item in scoped),
        "mfe_ge_2": sum(item.reached_two_pct for item in scoped),
        "mfe_ge_3": sum(item.reached_three_pct for item in scoped),
        "mfe_ge_5": sum(item.reached_five_pct for item in scoped),
        "mfe_ge_10": sum(item.reached_ten_pct for item in scoped),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(path: Path, summary_rows: list[dict[str, Any]]) -> None:
    all9 = {row["policy_id"]: row for row in summary_rows if row["scope"] == "ALL9"}
    holdout = {
        row["policy_id"]: row
        for row in summary_rows
        if row["scope"] == "HOLDOUT7"
    }
    lines = [
        "# P47H — Trailing Stop Ladder Exploration",
        "",
        "Exploratory development on the existing nine-asset sample.",
        "The new post-1% trailing spacing is NOT OOS-validated here.",
        "Use newly added assets/time as the next clean holdout after freezing a candidate.",
        "",
        "## Frozen ladder interpretation",
        "",
        "- initial stop: -1.00%",
        "- +0.10% MFE -> stop 0.00%",
        "- +0.20% -> +0.10%, ... +1.00% -> +0.90%",
        "- above +1.00%: staircase spacing 0.20 / 0.25 / 0.30%",
        "- stop is monotonic: it is never loosened",
        "- controls: A simple +1%; B full runner MFE giveback 1.50%",
        "",
        "## ALL9 pooled",
        "",
        "| policy | gross % | mean % | PF | max DD % | initial SL | trailing exits | 30d eq % |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for policy_id in ALL_POLICY_IDS:
        row = all9[policy_id]
        lines.append(
            f"| {policy_id} | {row['gross_sum_pct']} | {row['mean_trade_pct']} | "
            f"{row['profit_factor']} | {row['max_drawdown_pct']} | "
            f"{row['initial_stop_exits']} | {row['trailing_stop_exits']} | "
            f"{row['fixed_notional_30d_equivalent_pct']} |"
        )
    lines.extend(
        [
            "",
            "## HOLDOUT7 pooled (diagnostic reuse only)",
            "",
            "| policy | gross % | mean % | PF | max DD % |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for policy_id in ALL_POLICY_IDS:
        row = holdout[policy_id]
        lines.append(
            f"| {policy_id} | {row['gross_sum_pct']} | {row['mean_trade_pct']} | "
            f"{row['profit_factor']} | {row['max_drawdown_pct']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrail",
            "",
            "Do not call the winning 0.20/0.25/0.30 spacing OOS-validated.",
            "This run selects a candidate; the next untouched assets/time must validate it.",
            "Fees/slippage/funding and overlapping portfolio positions are not modeled here.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _validate_counts(signals_by_symbol: dict[str, int]) -> None:
    if signals_by_symbol != EXPECTED_COUNTS:
        raise RuntimeError(
            f"Frozen ALL9 source-count mismatch: actual={signals_by_symbol} "
            f"expected={EXPECTED_COUNTS}"
        )
    if sum(signals_by_symbol.values()) != EXPECTED_ALL9:
        raise RuntimeError("Frozen ALL9 pooled count mismatch")
    if sum(signals_by_symbol[symbol] for symbol in DEV_SYMBOLS) != EXPECTED_DEV2:
        raise RuntimeError("Frozen DEV2 pooled count mismatch")
    if sum(signals_by_symbol[symbol] for symbol in HOLDOUT_SYMBOLS) != EXPECTED_HOLDOUT7:
        raise RuntimeError("Frozen HOLDOUT7 pooled count mismatch")


def run_research(root: Path, output_dir: Path, config: LadderConfig) -> dict[str, Any]:
    sources = discover_sources(root)
    source_by_symbol = {source.symbol: source for source in sources}
    signals_by_symbol = {
        symbol: tuple(
            sorted(
                load_core_signals(source_by_symbol[symbol]),
                key=lambda item: item.touch_at,
            )
        )
        for symbol in ALL_SYMBOLS
    }
    counts = {symbol: len(signals_by_symbol[symbol]) for symbol in ALL_SYMBOLS}
    _validate_counts(counts)

    archive_by_symbol = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    cache_by_symbol = {
        symbol: TradeDayCache(max_days=config.day_cache_size) for symbol in ALL_SYMBOLS
    }
    ordered_signals = [
        signal
        for symbol in ALL_SYMBOLS
        for signal in signals_by_symbol[symbol]
    ]
    ordered_signals.sort(key=lambda item: item.touch_at)

    output_dir.mkdir(parents=True, exist_ok=True)
    reporter = ProgressReporter(config.progress_interval_seconds)
    reporter.emit(
        processed=0,
        total=len(ordered_signals),
        force=True,
        detail="build each 72h path once; simulate controls + 3 ladders",
    )

    results: list[PolicyResult] = []
    min_touch = min(signal.touch_at for signal in ordered_signals)
    max_touch = max(signal.touch_at for signal in ordered_signals)
    sample_span_days = max(1e-9, (max_touch - min_touch).total_seconds() / 86400.0)

    for index, signal in enumerate(ordered_signals, start=1):
        path = build_path_series(
            signal,
            archive_by_symbol[signal.symbol],
            horizon_hours=config.horizon_hours,
            cache=cache_by_symbol[signal.symbol],
        )
        results.extend(simulate_policies(path, config))
        cache = cache_by_symbol[signal.symbol]
        reporter.emit(
            processed=index,
            total=len(ordered_signals),
            detail=(
                f"symbol={signal.symbol} scope={_scope_for_symbol(signal.symbol)} "
                f"cache_hits={cache.hits} cache_misses={cache.misses}"
            ),
        )

    summary_rows: list[dict[str, Any]] = []
    scopes = ("ALL9", "DEV2", "HOLDOUT7", *ALL_SYMBOLS)
    for scope in scopes:
        for policy_id in ALL_POLICY_IDS:
            summary_rows.append(
                summarize_scope(
                    results,
                    scope=scope,
                    policy_id=policy_id,
                    sample_span_days=sample_span_days,
                )
            )

    result_rows = [asdict(item) for item in results]
    _write_csv(output_dir / "policy_results.csv", result_rows)
    _write_csv(output_dir / "policy_summary.csv", summary_rows)
    _write_summary_md(output_dir / "summary.md", summary_rows)

    all9_rows = [row for row in summary_rows if row["scope"] == "ALL9"]
    best_ladder = max(
        (row for row in all9_rows if row["policy_id"] in LADDER_POLICY_IDS),
        key=lambda row: float(row["gross_sum_pct"]),
    )
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "config": asdict(config),
        "source_counts": counts,
        "sample_span_days": round(sample_span_days, 6),
        "all9": all9_rows,
        "best_ladder_by_all9_gross": best_ladder,
        "methodology": {
            "status": "exploratory_existing_sample",
            "new_parameter_search": True,
            "next_validation": "freeze candidate, validate on untouched new assets/time",
        },
    }
    (output_dir / "summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    reporter.emit(
        processed=len(ordered_signals),
        total=len(ordered_signals),
        force=True,
        detail="done",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="P47H trailing stop ladder exploration")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--day-cache-size", type=int, default=6)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    args = parser.parse_args()

    root = args.root.resolve()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "trailing_ladder_v1" / f"ALL9_{stamp}"

    config = LadderConfig(
        horizon_hours=args.horizon_hours,
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    summary = run_research(root, output_dir.resolve(), config)
    pooled = {row["policy_id"]: row for row in summary["all9"]}
    print("P47H ALL9 gross:")
    for policy_id in ALL_POLICY_IDS:
        print(f"  {policy_id}: {pooled[policy_id]['gross_sum_pct']}%")
    best = summary["best_ladder_by_all9_gross"]
    print(
        "P47H best ladder: "
        f"{best['policy_id']} gross={best['gross_sum_pct']}% "
        f"PF={best['profit_factor']} DD={best['max_drawdown_pct']}%"
    )
    print(f"Report: {output_dir.resolve() / 'summary.json'}")
    print(f"Readable summary: {output_dir.resolve() / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
