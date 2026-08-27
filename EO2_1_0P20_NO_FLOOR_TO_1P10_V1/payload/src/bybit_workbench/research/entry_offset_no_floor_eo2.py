from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import statistics
import time
from array import array
from collections.abc import Callable, Sequence
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
from bybit_workbench.research.exit_break_even_v13 import TradeDayCache
from bybit_workbench.research.flow_reversal_v1 import TradeDay, _archive_map, _load_trade_day
from bybit_workbench.research.mtf_entry import Direction

RESEARCH_VERSION = "EO2_ADVERSE_0P20_NO_FLOOR_TO_1P10_V1"
ENGINE_REVISION = "EO2_1_VECTOR_DAY_FIRST_HIT"
SOURCE_RESEARCH = "EO1_ADVERSE_ENTRY_OFFSET_REPLAY_V1"
SOURCE_ENGINE = "EO1_2_MEMORY_BOUNDED_STREAMING"
SOURCE_SCENARIO = "ADVERSE_0P20"
SOURCE_EVENT_SHA256 = "91044aba6f3148e6599a5ce9a7a1414126d19a9cbed28983e19f753203b1d44f"
EXPECTED_SOURCE_SIGNALS = 1063
EXPECTED_SOURCE_EVENT_ROWS = 3189
EXPECTED_FILLED_0P20 = 846
TARGET_PCT = 1.10
INITIAL_STOP_PCT = 1.00
COST_RESERVE_PCT = 0.10
MARGIN_USD = 100.0
LEVERAGE = 10.0
FROZEN_END = datetime(2026, 8, 16, tzinfo=UTC)

Outcome = Literal["target", "initial_stop", "data_end_open"]


@dataclass(frozen=True, slots=True)
class SourceFill:
    symbol: str
    direction: Direction
    touch_at: datetime
    fill_at: datetime
    fill_price: float
    original_entry_price: float


@dataclass(frozen=True, slots=True)
class ReplayResult:
    symbol: str
    direction: Direction
    touch_at: datetime
    fill_at: datetime
    fill_price: float
    target_price: float
    stop_price: float
    outcome: Outcome
    exit_at: datetime | None
    duration_seconds: float | None
    duration_hours: float | None
    exit_price_observed: float | None
    theoretical_exit_level_pct: float | None
    gross_pnl_pct_theoretical: float | None
    net_pnl_pct_after_cost_reserve: float | None
    pnl_usd_100_margin_10x: float | None
    mfe_until_exit_or_data_end_pct: float
    mae_until_exit_or_data_end_pct: float
    last_observed_at: datetime
    last_observed_price: float
    last_move_from_fill_pct: float
    archive_days_scanned: int


class Progress:
    def __init__(self, interval_seconds: float = 20.0) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(self, processed: int, total: int, *, force: bool = False, detail: str = "") -> None:
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
            f"[EO2] processed={processed}/{total} ({100.0 * processed / max(1, total):.1f}%) "
            f"elapsed={_duration(elapsed)} ETA={'n/a' if eta is None else _duration(eta)}{suffix}",
            flush=True,
        )
        self.last_emit = now


def _duration(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_dt(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError(f"timestamp lacks timezone: {value}")
    return result.astimezone(UTC)


def _validate_source_report(report_dir: Path) -> Path:
    summary_path = report_dir / "summary.json"
    provenance_path = report_dir / "provenance.json"
    events_path = report_dir / "entry_offset_adverse_events.csv"
    for path in (summary_path, provenance_path, events_path):
        if not path.exists():
            raise FileNotFoundError(f"EO2 source report is missing: {path}")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    required = {
        "research": SOURCE_RESEARCH,
        "engine_revision": SOURCE_ENGINE,
        "signals": EXPECTED_SOURCE_SIGNALS,
        "event_rows": EXPECTED_SOURCE_EVENT_ROWS,
        "target_pct": TARGET_PCT,
        "initial_stop_pct": INITIAL_STOP_PCT,
    }
    for key, expected in required.items():
        if summary.get(key) != expected:
            raise ValueError(
                f"EO2 source summary mismatch for {key}: {summary.get(key)!r} != {expected!r}"
            )
    actual_hash = _sha256(events_path)
    if actual_hash != SOURCE_EVENT_SHA256:
        raise ValueError(
            "EO2 source event-table hash mismatch: "
            f"{actual_hash} != {SOURCE_EVENT_SHA256}"
        )
    if provenance.get("event_table_sha256") != actual_hash:
        raise ValueError("EO2 source provenance does not authenticate event table")
    return events_path


def load_source_fills(report_dir: Path) -> tuple[SourceFill, ...]:
    events_path = _validate_source_report(report_dir)
    fills: list[SourceFill] = []
    with events_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("scenario") != SOURCE_SCENARIO or row.get("fill_status") != "filled":
                continue
            fill_at_raw = row.get("fill_at") or ""
            fill_price_raw = row.get("fill_price_ideal") or ""
            if not fill_at_raw or not fill_price_raw:
                raise ValueError("filled EO1 0.20 row lacks fill metadata")
            direction = row.get("direction")
            if direction not in {"Long", "Short"}:
                raise ValueError(f"unknown direction: {direction!r}")
            fills.append(
                SourceFill(
                    symbol=str(row["symbol"]),
                    direction=cast(Direction, direction),
                    touch_at=_parse_dt(str(row["touch_at"])),
                    fill_at=_parse_dt(fill_at_raw),
                    fill_price=float(fill_price_raw),
                    original_entry_price=float(row["original_entry_price"]),
                )
            )
    if len(fills) != EXPECTED_FILLED_0P20:
        raise ValueError(f"EO2 expected {EXPECTED_FILLED_0P20} filled 0.20 rows, got {len(fills)}")
    keys = {(row.symbol, row.touch_at.isoformat()) for row in fills}
    if len(keys) != len(fills):
        raise ValueError("EO2 source fills contain duplicate signal keys")
    return tuple(sorted(fills, key=lambda row: (row.symbol, row.fill_at, row.touch_at)))


def _target_stop_prices(fill: SourceFill) -> tuple[float, float]:
    if fill.direction == "Long":
        return (
            fill.fill_price * (1.0 + TARGET_PCT / 100.0),
            fill.fill_price * (1.0 - INITIAL_STOP_PCT / 100.0),
        )
    return (
        fill.fill_price * (1.0 - TARGET_PCT / 100.0),
        fill.fill_price * (1.0 + INITIAL_STOP_PCT / 100.0),
    )


def _move_pct(direction: Direction, entry: float, price: float) -> float:
    raw = (price / entry - 1.0) * 100.0
    return raw if direction == "Long" else -raw


def _dates_until_frozen_end(
    start: datetime, *, data_end: datetime = FROZEN_END
) -> tuple[str, ...]:
    if start >= data_end:
        return ()
    current = start.date()
    last = (data_end - timedelta(microseconds=1)).date()
    days: list[str] = []
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(days)


def _as_float64(values: Sequence[float]) -> NDArray[np.float64]:
    if isinstance(values, array) and values.typecode == "d":
        return np.frombuffer(values, dtype=np.float64)
    return np.asarray(values, dtype=np.float64)


def _first_hit_in_segment(
    prices: NDArray[np.float64],
    *,
    direction: Direction,
    target_price: float,
    stop_price: float,
) -> tuple[int, Outcome] | None:
    if prices.size == 0:
        return None
    if direction == "Long":
        mask = np.logical_or(prices >= target_price, prices <= stop_price)
    else:
        mask = np.logical_or(prices <= target_price, prices >= stop_price)
    hits = np.flatnonzero(mask)
    if hits.size == 0:
        return None
    index = int(hits[0])
    price = float(prices[index])
    if direction == "Long":
        outcome: Outcome = "target" if price >= target_price else "initial_stop"
    else:
        outcome = "target" if price <= target_price else "initial_stop"
    return index, outcome


def replay_fill(
    fill: SourceFill,
    archive_by_day: dict[str, Path],
    *,
    cache: TradeDayCache,
    data_end: datetime = FROZEN_END,
) -> ReplayResult:
    days = _dates_until_frozen_end(fill.fill_at, data_end=data_end)
    if not days:
        raise ValueError(f"EO2 fill is outside frozen period: {fill.symbol} {fill.fill_at}")
    target_price, stop_price = _target_stop_prices(fill)
    fill_ts = fill.fill_at.timestamp()
    frozen_end_ts = data_end.timestamp()
    mfe = -math.inf
    mae = math.inf
    last_ts: float | None = None
    last_price: float | None = None
    scanned = 0

    for day in days:
        if day not in archive_by_day:
            raise FileNotFoundError(
                f"EO2 archive gap before resolution for {fill.symbol} {fill.fill_at}: {day}"
            )
        tape = cache.get(archive_by_day[day])
        timestamps = _as_float64(tape.timestamps)
        prices = _as_float64(tape.prices)
        start_index = bisect.bisect_left(tape.timestamps, fill_ts)
        end_index = bisect.bisect_left(tape.timestamps, frozen_end_ts)
        if end_index <= start_index:
            continue
        scanned += 1
        segment_prices = prices[start_index:end_index]
        hit = _first_hit_in_segment(
            segment_prices,
            direction=fill.direction,
            target_price=target_price,
            stop_price=stop_price,
        )
        stop_at = segment_prices.size if hit is None else hit[0] + 1
        observed = segment_prices[:stop_at]
        if observed.size:
            if fill.direction == "Long":
                local_mfe = (float(np.max(observed)) / fill.fill_price - 1.0) * 100.0
                local_mae = (float(np.min(observed)) / fill.fill_price - 1.0) * 100.0
            else:
                local_mfe = _move_pct(
                    fill.direction, fill.fill_price, float(np.min(observed))
                )
                local_mae = _move_pct(
                    fill.direction, fill.fill_price, float(np.max(observed))
                )
            mfe = max(mfe, local_mfe)
            mae = min(mae, local_mae)
            last_index = start_index + stop_at - 1
            last_ts = float(timestamps[last_index])
            last_price = float(prices[last_index])

        if hit is not None:
            relative_index, outcome = hit
            absolute_index = start_index + relative_index
            event_ts = float(timestamps[absolute_index])
            event_price = float(prices[absolute_index])
            level = TARGET_PCT if outcome == "target" else -INITIAL_STOP_PCT
            net_pct = level - COST_RESERVE_PCT
            pnl_usd = MARGIN_USD * LEVERAGE * net_pct / 100.0
            duration_seconds = max(0.0, event_ts - fill_ts)
            return ReplayResult(
                symbol=fill.symbol,
                direction=fill.direction,
                touch_at=fill.touch_at,
                fill_at=fill.fill_at,
                fill_price=fill.fill_price,
                target_price=target_price,
                stop_price=stop_price,
                outcome=outcome,
                exit_at=datetime.fromtimestamp(event_ts, UTC),
                duration_seconds=duration_seconds,
                duration_hours=duration_seconds / 3600.0,
                exit_price_observed=event_price,
                theoretical_exit_level_pct=level,
                gross_pnl_pct_theoretical=level,
                net_pnl_pct_after_cost_reserve=net_pct,
                pnl_usd_100_margin_10x=pnl_usd,
                mfe_until_exit_or_data_end_pct=mfe,
                mae_until_exit_or_data_end_pct=mae,
                last_observed_at=datetime.fromtimestamp(event_ts, UTC),
                last_observed_price=event_price,
                last_move_from_fill_pct=_move_pct(fill.direction, fill.fill_price, event_price),
                archive_days_scanned=scanned,
            )

    if last_ts is None or last_price is None or not math.isfinite(mfe) or not math.isfinite(mae):
        raise ValueError(f"EO2 found no observations after fill: {fill.symbol} {fill.fill_at}")
    return ReplayResult(
        symbol=fill.symbol,
        direction=fill.direction,
        touch_at=fill.touch_at,
        fill_at=fill.fill_at,
        fill_price=fill.fill_price,
        target_price=target_price,
        stop_price=stop_price,
        outcome="data_end_open",
        exit_at=None,
        duration_seconds=None,
        duration_hours=None,
        exit_price_observed=None,
        theoretical_exit_level_pct=None,
        gross_pnl_pct_theoretical=None,
        net_pnl_pct_after_cost_reserve=None,
        pnl_usd_100_margin_10x=None,
        mfe_until_exit_or_data_end_pct=mfe,
        mae_until_exit_or_data_end_pct=mae,
        last_observed_at=datetime.fromtimestamp(last_ts, UTC),
        last_observed_price=last_price,
        last_move_from_fill_pct=_move_pct(fill.direction, fill.fill_price, last_price),
        archive_days_scanned=scanned,
    )


def _trade_loader(symbol: str, heartbeat_seconds: float) -> Callable[[Path], TradeDay]:
    def load(path: Path) -> TradeDay:
        return _load_trade_day(
            path,
            progress_label=f"{symbol}/{path.name}",
            heartbeat_seconds=heartbeat_seconds,
            progress_sink=lambda text: print(text.replace("[P31 tape]", "[EO2 tape]"), flush=True),
        )

    return load


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def _duration_buckets(rows: Sequence[ReplayResult], outcome: Outcome) -> dict[str, int]:
    limits = (
        ("le_5m", 5 * 60.0),
        ("le_15m", 15 * 60.0),
        ("le_30m", 30 * 60.0),
        ("le_1h", 3600.0),
        ("le_3h", 3 * 3600.0),
        ("le_6h", 6 * 3600.0),
        ("le_12h", 12 * 3600.0),
        ("le_24h", 24 * 3600.0),
        ("le_48h", 48 * 3600.0),
        ("le_72h", 72 * 3600.0),
    )
    durations = [
        float(row.duration_seconds)
        for row in rows
        if row.outcome == outcome and row.duration_seconds is not None
    ]
    result = {name: sum(value <= limit for value in durations) for name, limit in limits}
    result["gt_72h"] = sum(value > 72 * 3600.0 for value in durations)
    return result


def summarize(rows: Sequence[ReplayResult]) -> dict[str, Any]:
    targets = [row for row in rows if row.outcome == "target"]
    stops = [row for row in rows if row.outcome == "initial_stop"]
    opens = [row for row in rows if row.outcome == "data_end_open"]
    resolved = targets + stops
    pnl_values = [
        float(row.pnl_usd_100_margin_10x)
        for row in resolved
        if row.pnl_usd_100_margin_10x is not None
    ]
    profits = sum(value for value in pnl_values if value > 0)
    losses = -sum(value for value in pnl_values if value < 0)
    pf = profits / losses if losses > 0 else (math.inf if profits > 0 else None)
    target_durations = [
        float(row.duration_seconds)
        for row in targets
        if row.duration_seconds is not None
    ]
    stop_durations = [
        float(row.duration_seconds)
        for row in stops
        if row.duration_seconds is not None
    ]
    aggregate = sum(pnl_values)
    return {
        "research": RESEARCH_VERSION,
        "engine_revision": ENGINE_REVISION,
        "source_scenario": SOURCE_SCENARIO,
        "filled_trades": len(rows),
        "target_plus_1p10": len(targets),
        "target_rate_per_fill_pct": 100.0 * len(targets) / len(rows) if rows else 0.0,
        "initial_stop_minus_1p00": len(stops),
        "stop_rate_per_fill_pct": 100.0 * len(stops) / len(rows) if rows else 0.0,
        "data_end_open": len(opens),
        "resolved_trades": len(resolved),
        "resolved_win_rate_pct": 100.0 * len(targets) / len(resolved) if resolved else None,
        "break_even_win_rate_pct_at_10_win_11_loss": 100.0 * 11.0 / 21.0,
        "aggregate_net_usd_fixed_100_margin_10x_resolved": aggregate,
        "ev_usd_per_resolved_trade": aggregate / len(resolved) if resolved else None,
        "realized_contribution_usd_per_filled_signal": aggregate / len(rows) if rows else None,
        "profit_factor": pf,
        "target_duration_median_hours": (
            None
            if not target_durations
            else statistics.median(target_durations) / 3600.0
        ),
        "target_duration_p75_hours": (
            None if not target_durations else _quantile(target_durations, 0.75) / 3600.0
        ),
        "target_duration_p90_hours": (
            None if not target_durations else _quantile(target_durations, 0.90) / 3600.0
        ),
        "target_duration_p95_hours": (
            None if not target_durations else _quantile(target_durations, 0.95) / 3600.0
        ),
        "target_duration_max_hours": (
            None if not target_durations else max(target_durations) / 3600.0
        ),
        "stop_duration_median_hours": (
            None if not stop_durations else statistics.median(stop_durations) / 3600.0
        ),
        "stop_duration_p90_hours": (
            None if not stop_durations else _quantile(stop_durations, 0.90) / 3600.0
        ),
        "target_duration_buckets": _duration_buckets(rows, "target"),
        "stop_duration_buckets": _duration_buckets(rows, "initial_stop"),
        "frozen_end": FROZEN_END.isoformat(),
        "target_pct": TARGET_PCT,
        "initial_stop_pct": INITIAL_STOP_PCT,
        "positive_floor": "DISABLED",
        "illustrative_round_trip_cost_pct": COST_RESERVE_PCT,
        "margin_usd": MARGIN_USD,
        "leverage": LEVERAGE,
        "downloads": "DISABLED",
        "production_effect": "NONE",
    }


def _per_symbol(rows: Sequence[ReplayResult]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for symbol in ALL_SYMBOLS:
        subset = [row for row in rows if row.symbol == symbol]
        summary = summarize(subset)
        output.append(
            {
                "symbol": symbol,
                "filled_trades": summary["filled_trades"],
                "target_plus_1p10": summary["target_plus_1p10"],
                "target_rate_per_fill_pct": summary["target_rate_per_fill_pct"],
                "initial_stop_minus_1p00": summary["initial_stop_minus_1p00"],
                "stop_rate_per_fill_pct": summary["stop_rate_per_fill_pct"],
                "data_end_open": summary["data_end_open"],
                "resolved_win_rate_pct": summary["resolved_win_rate_pct"],
                "aggregate_net_usd_fixed_100_margin_10x_resolved": summary[
                    "aggregate_net_usd_fixed_100_margin_10x_resolved"
                ],
                "ev_usd_per_resolved_trade": summary["ev_usd_per_resolved_trade"],
                "profit_factor": summary["profit_factor"],
                "target_duration_median_hours": summary["target_duration_median_hours"],
            }
        )
    return output


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _write_events(path: Path, rows: Sequence[ReplayResult]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            raw = asdict(row)
            writer.writerow(
                {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in raw.items()
                }
            )


def _summary_md(summary: dict[str, Any], per_symbol: Sequence[dict[str, Any]]) -> str:
    pf = summary["profit_factor"]
    pf_text = "inf" if pf == math.inf else "n/a" if pf is None else f"{float(pf):.3f}"
    lines = [
        "# EO2 -0.20 Entry, no +0.10 floor, wait for +1.10 or -1.00",
        "",
        "Research-only signal replay on exactly the 846 EO1 ADVERSE_0P20 fills.",
        "No +0.10 activation/floor. Initial -1.00 stop remains. Target is +1.10.",
        "Replay continues causally until the first target/stop tick or frozen-data end.",
        "",
        "## ALL9",
        "",
        f"- Filled: **{summary['filled_trades']}**",
        (
            f"- +1.10 target first: **{summary['target_plus_1p10']}** "
            f"({float(summary['target_rate_per_fill_pct']):.2f}%)"
        ),
        (
            f"- -1.00 stop first: **{summary['initial_stop_minus_1p00']}** "
            f"({float(summary['stop_rate_per_fill_pct']):.2f}%)"
        ),
        f"- Still open at frozen-data end: **{summary['data_end_open']}**",
        f"- Resolved win rate: **{float(summary['resolved_win_rate_pct'] or 0.0):.2f}%**",
        (
            "- Break-even win rate for +$10 / -$11: "
            f"**{float(summary['break_even_win_rate_pct_at_10_win_11_loss']):.2f}%**"
        ),
        (
            "- Aggregate theoretical net, fixed $100 margin x10, resolved only: "
            f"**${float(summary['aggregate_net_usd_fixed_100_margin_10x_resolved']):.2f}**"
        ),
        f"- EV per resolved trade: **${float(summary['ev_usd_per_resolved_trade'] or 0.0):.3f}**",
        f"- PF: **{pf_text}**",
        (
            "- Median time to +1.10: "
            f"**{float(summary['target_duration_median_hours'] or 0.0):.2f} h**"
        ),
        f"- P90 time to +1.10: **{float(summary['target_duration_p90_hours'] or 0.0):.2f} h**",
        (
            "- Max time to +1.10 inside frozen data: "
            f"**{float(summary['target_duration_max_hours'] or 0.0):.2f} h**"
        ),
        "",
        "## Per symbol",
        "",
        (
            "| Symbol | Fill | Target | Stop | Open | Win % resolved | Net $ | PF | "
            "Median h to target |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in per_symbol:
        row_pf = row["profit_factor"]
        row_pf_text = (
            "inf"
            if row_pf == math.inf
            else "n/a" if row_pf is None else f"{float(row_pf):.3f}"
        )
        win = row["resolved_win_rate_pct"]
        median = row["target_duration_median_hours"]
        lines.append(
            f"| {row['symbol']} | {row['filled_trades']} | {row['target_plus_1p10']} | "
            f"{row['initial_stop_minus_1p00']} | {row['data_end_open']} | "
            f"{'' if win is None else f'{float(win):.2f}'} | "
            f"{float(row['aggregate_net_usd_fixed_100_margin_10x_resolved']):.2f} | "
            f"{row_pf_text} | {'' if median is None else f'{float(median):.2f}'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            (
                "- This is signal replay, not a portfolio backtest. Overlapping same-symbol "
                "trades are not capital-constrained here."
            ),
            (
                "- +$10 / -$11 uses EO1's illustrative 0.10% round-trip cost reserve "
                "on $100 margin x10."
            ),
            (
                "- It does not model exact Bybit fees, funding, slippage, margin conflicts, "
                "or portfolio chronology."
            ),
            "- Open trades at 2026-08-16T00:00:00Z are censored and are not assigned realized PnL.",
            "- Downloads are disabled; missing local raw-trade days fail closed.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(
    project_root: Path,
    source_report_dir: Path,
    output_dir: Path,
    *,
    day_cache_size: int,
    progress_interval_seconds: float,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    fills = load_source_fills(source_report_dir)
    sources = discover_sources(project_root)
    source_by_symbol = {source.symbol: source for source in sources}
    archive_maps = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    caches = {
        symbol: TradeDayCache(
            max_days=day_cache_size,
            loader=_trade_loader(symbol, progress_interval_seconds),
        )
        for symbol in ALL_SYMBOLS
    }

    progress = Progress(progress_interval_seconds)
    progress.emit(0, len(fills), force=True, detail="exact EO1 -0.20 fills")
    rows: list[ReplayResult] = []
    for index, fill in enumerate(fills, start=1):
        result = replay_fill(fill, archive_maps[fill.symbol], cache=caches[fill.symbol])
        rows.append(result)
        progress.emit(index, len(fills), detail=f"{fill.symbol} {fill.fill_at.isoformat()}")
    progress.emit(len(fills), len(fills), force=True, detail="complete")

    if len(rows) != EXPECTED_FILLED_0P20:
        raise ValueError(f"EO2 result count mismatch: {len(rows)} != {EXPECTED_FILLED_0P20}")

    summary = summarize(rows)
    per_symbol = _per_symbol(rows)
    events_path = output_dir / "eo2_events.csv"
    summary_json_path = output_dir / "summary.json"
    per_symbol_path = output_dir / "per_symbol.csv"
    _write_events(events_path, rows)
    _write_csv(per_symbol_path, per_symbol)
    summary_json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_dir / "SUMMARY_RU.md").write_text(_summary_md(summary, per_symbol), encoding="utf-8")

    provenance = {
        **summary,
        "completed_at": datetime.now(UTC).isoformat(),
        "period_tag": PERIOD_TAG,
        "source_report_dir": str(source_report_dir),
        "source_event_sha256": SOURCE_EVENT_SHA256,
        "source_expected_filled_0p20": EXPECTED_FILLED_0P20,
        "events_sha256": _sha256(events_path),
        "per_symbol_sha256": _sha256(per_symbol_path),
        "summary_sha256": _sha256(summary_json_path),
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EO2 replay of exact EO1 -0.20 fills without +0.10 floor"
    )
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--source-report-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--day-cache-size", type=int, default=10)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if int(args.day_cache_size) <= 0:
        raise ValueError("day-cache-size must be positive")
    if float(args.progress_interval_seconds) <= 0:
        raise ValueError("progress interval must be positive")
    run(
        args.project_root.resolve(),
        args.source_report_dir.resolve(),
        args.output_dir.resolve(),
        day_cache_size=int(args.day_cache_size),
        progress_interval_seconds=float(args.progress_interval_seconds),
    )


if __name__ == "__main__":
    main()
