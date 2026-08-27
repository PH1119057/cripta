from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import statistics
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

P47K_REPORT_ROOT = Path("reports/early_protection_plus05_minus05_v1")
P47G_REPORT_ROOT = Path("reports/early_failure_puncture_v1")
EXPECTED_P47K_COUNTS = {
    "baseline_initial_stop": 66,
    "initial_stop_before_0p50": 192,
    "floor_minus_0p50": 275,
    "reached_1p10": 521,
    "data_end_no_activation": 9,
}
EXPECTED_P47G_FAILURES = 66

Outcome = Literal[
    "baseline_initial_stop",
    "initial_stop_before_0p50",
    "floor_minus_0p50",
    "reached_1p10",
]
SkipReason = Literal[
    "same_symbol_open",
    "no_capacity",
    "burst_cap",
    "insufficient_margin",
    "censored_tail",
]


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    starting_bank_usd: Decimal = Decimal("100")
    leverage: Decimal = Decimal("10")
    slot_fractions: tuple[Decimal, ...] = (
        Decimal("0.50"),
        Decimal("0.30"),
        Decimal("0.20"),
    )
    maker_fee_rate: Decimal = Decimal("0.00020")
    taker_fee_rate: Decimal = Decimal("0.00055")
    local_timezone_offset_hours: int = 5
    burst_window_minutes: int = 15
    burst_max_entries: int = 2

    def __post_init__(self) -> None:
        if self.starting_bank_usd <= 0:
            raise ValueError("starting_bank_usd must be positive")
        if self.leverage <= 0:
            raise ValueError("leverage must be positive")
        if not self.slot_fractions or any(value <= 0 for value in self.slot_fractions):
            raise ValueError("slot fractions must be positive")
        if sum(self.slot_fractions, Decimal("0")) > Decimal("1"):
            raise ValueError("slot fractions may not exceed 100% of the reference bank")
        if self.maker_fee_rate < 0 or self.taker_fee_rate < 0:
            raise ValueError("fee rates may not be negative")
        if not -23 <= self.local_timezone_offset_hours <= 23:
            raise ValueError("invalid timezone offset")
        if self.burst_window_minutes <= 0 or self.burst_max_entries <= 0:
            raise ValueError("invalid burst-cap configuration")


@dataclass(frozen=True, slots=True)
class SignalEvent:
    symbol: str
    entry_at: datetime
    exit_at: datetime
    outcome: Outcome
    move_pct: Decimal
    old_exit_reason: str


@dataclass(frozen=True, slots=True)
class OpenTrade:
    trade_id: int
    policy_id: str
    symbol: str
    entry_at: datetime
    exit_at: datetime
    outcome: Outcome
    move_pct: Decimal
    slot_index: int
    slot_fraction: Decimal
    margin_usd: Decimal
    notional_usd: Decimal
    entry_fee_usd: Decimal
    exit_fee_usd: Decimal
    gross_pnl_usd: Decimal
    net_pnl_usd: Decimal
    old_exit_reason: str


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    trade_id: int
    policy_id: str
    symbol: str
    entry_at: datetime
    exit_at: datetime
    outcome: Outcome
    move_pct: Decimal
    slot_index: int
    slot_fraction: Decimal
    margin_usd: Decimal
    notional_usd: Decimal
    entry_fee_usd: Decimal
    exit_fee_usd: Decimal
    gross_pnl_usd: Decimal
    net_pnl_usd: Decimal
    equity_after_exit_usd: Decimal
    old_exit_reason: str


@dataclass(frozen=True, slots=True)
class SkippedSignal:
    policy_id: str
    symbol: str
    entry_at: datetime
    exit_at: datetime | None
    reason: SkipReason
    outcome: str


@dataclass(frozen=True, slots=True)
class PolicyResult:
    policy_id: str
    executed: tuple[ClosedTrade, ...]
    skipped: tuple[SkippedSignal, ...]
    starting_bank_usd: Decimal
    ending_equity_usd: Decimal
    total_gross_pnl_usd: Decimal
    total_fees_usd: Decimal
    total_net_pnl_usd: Decimal
    max_realized_drawdown_usd: Decimal
    max_realized_drawdown_pct: Decimal
    max_open_positions: int
    occupancy_seconds: dict[int, float]
    slot_busy_seconds: dict[int, float]
    replay_start: datetime
    replay_end: datetime


@dataclass(frozen=True, slots=True)
class SourceInfo:
    name: str
    path: str
    sha256: str


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _latest_report(root: Path, relative_root: Path, pattern: str) -> Path:
    base = root / relative_root
    candidates = sorted(path for path in base.glob(pattern) if path.is_dir())
    if not candidates:
        raise FileNotFoundError(f"required report not found under {base}: {pattern}")
    return candidates[-1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_p47k(path: Path) -> tuple[list[dict[str, str]], datetime]:
    rows: list[dict[str, str]] = []
    counts: dict[str, int] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            row = {key: str(value or "") for key, value in raw.items()}
            outcome = row.get("outcome", "")
            counts[outcome] = counts.get(outcome, 0) + 1
            rows.append(row)
    if counts != EXPECTED_P47K_COUNTS:
        raise ValueError(f"P47K guardrail failed: {counts}")
    censored = [_parse_dt(row["touch_at"]) for row in rows if row["outcome"].startswith("data_end")]
    if not censored:
        raise ValueError("P47K contains no censored-tail rows")
    return rows, min(censored)


def _load_p47g_stop_times(path: Path) -> dict[tuple[str, datetime], datetime]:
    result: dict[tuple[str, datetime], datetime] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol") or "")
            entry_at = _parse_dt(str(row.get("touch_at") or ""))
            stop_at = _parse_dt(str(row.get("first_minus_1_at") or ""))
            key = (symbol, entry_at)
            if key in result:
                raise ValueError(f"duplicate P47G key: {key}")
            result[key] = stop_at
    if len(result) != EXPECTED_P47G_FAILURES:
        raise ValueError(f"P47G guardrail failed: {len(result)} != {EXPECTED_P47G_FAILURES}")
    return result


def load_signal_events(
    root: Path,
) -> tuple[tuple[SignalEvent, ...], datetime, tuple[SourceInfo, ...]]:
    p47k_dir = _latest_report(root, P47K_REPORT_ROOT, "ALL9_*")
    p47g_dir = _latest_report(root, P47G_REPORT_ROOT, "ALL9_*")
    p47k_csv = p47k_dir / "event_results.csv"
    p47g_csv = p47g_dir / "early_failure_events.csv"
    if not p47k_csv.is_file() or not p47g_csv.is_file():
        raise FileNotFoundError("required compact P47K/P47G CSV is missing")

    p47k_rows, cutoff = _load_p47k(p47k_csv)
    p47g_stops = _load_p47g_stop_times(p47g_csv)
    events: list[SignalEvent] = []
    baseline_matches = 0
    for row in p47k_rows:
        entry_at = _parse_dt(row["touch_at"])
        if entry_at >= cutoff:
            continue
        outcome_raw = row["outcome"]
        if outcome_raw == "baseline_initial_stop":
            key = (row["symbol"], entry_at)
            exit_at = p47g_stops.get(key)
            if exit_at is None:
                raise ValueError(f"missing P47G stop timestamp for {key}")
            baseline_matches += 1
            outcome: Outcome = "baseline_initial_stop"
            move_pct = Decimal("-1.00")
        elif outcome_raw == "initial_stop_before_0p50":
            exit_at = _parse_dt(row["event_at"])
            outcome = "initial_stop_before_0p50"
            move_pct = Decimal("-1.00")
        elif outcome_raw == "floor_minus_0p50":
            exit_at = _parse_dt(row["event_at"])
            outcome = "floor_minus_0p50"
            move_pct = Decimal("-0.50")
        elif outcome_raw == "reached_1p10":
            exit_at = _parse_dt(row["event_at"])
            outcome = "reached_1p10"
            move_pct = Decimal("1.10")
        else:
            continue
        if exit_at < entry_at:
            raise ValueError(f"exit precedes entry for {row['symbol']} {entry_at.isoformat()}")
        events.append(
            SignalEvent(
                symbol=row["symbol"],
                entry_at=entry_at,
                exit_at=exit_at,
                outcome=outcome,
                move_pct=move_pct,
                old_exit_reason=row.get("old_exit_reason", ""),
            )
        )
    expected_baseline_before_cutoff = sum(
        1
        for row in p47k_rows
        if row["outcome"] == "baseline_initial_stop" and _parse_dt(row["touch_at"]) < cutoff
    )
    if baseline_matches != expected_baseline_before_cutoff:
        raise ValueError(
            "baseline stop timestamp merge failed: "
            f"{baseline_matches} != {expected_baseline_before_cutoff}"
        )
    events.sort(key=lambda item: (item.entry_at, item.symbol))
    if not events:
        raise ValueError("no complete signals before censored tail")
    sources = (
        SourceInfo("P47K event_results", str(p47k_csv.resolve()), _sha256(p47k_csv)),
        SourceInfo("P47G early_failure_events", str(p47g_csv.resolve()), _sha256(p47g_csv)),
    )
    return tuple(events), cutoff, sources


def _exit_fee_rate(outcome: Outcome, config: ReplayConfig) -> Decimal:
    if outcome == "reached_1p10":
        return config.maker_fee_rate
    return config.taker_fee_rate


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _make_trade(
    trade_id: int,
    policy_id: str,
    signal: SignalEvent,
    slot_index: int,
    sizing_bank_usd: Decimal,
    config: ReplayConfig,
) -> OpenTrade:
    slot_fraction = config.slot_fractions[slot_index]
    slot_budget = sizing_bank_usd * slot_fraction
    fee_multiplier = Decimal("1") + config.leverage * config.maker_fee_rate
    margin = slot_budget / fee_multiplier
    notional = margin * config.leverage
    entry_fee = _money(notional * config.maker_fee_rate)
    exit_fee = _money(notional * _exit_fee_rate(signal.outcome, config))
    gross = _money(notional * signal.move_pct / Decimal("100"))
    margin_rounded = _money(margin)
    notional_rounded = _money(notional)
    net = gross - entry_fee - exit_fee
    return OpenTrade(
        trade_id=trade_id,
        policy_id=policy_id,
        symbol=signal.symbol,
        entry_at=signal.entry_at,
        exit_at=signal.exit_at,
        outcome=signal.outcome,
        move_pct=signal.move_pct,
        slot_index=slot_index,
        slot_fraction=slot_fraction,
        margin_usd=margin_rounded,
        notional_usd=notional_rounded,
        entry_fee_usd=entry_fee,
        exit_fee_usd=exit_fee,
        gross_pnl_usd=gross,
        net_pnl_usd=_money(net),
        old_exit_reason=signal.old_exit_reason,
    )


def replay_policy(
    signals: Iterable[SignalEvent],
    config: ReplayConfig,
    *,
    policy_id: str,
    use_burst_cap: bool,
) -> PolicyResult:
    ordered = tuple(sorted(signals, key=lambda item: (item.entry_at, item.symbol)))
    if not ordered:
        raise ValueError("signals may not be empty")

    open_by_slot: dict[int, OpenTrade] = {}
    open_symbol_to_slot: dict[str, int] = {}
    recent_entries: list[datetime] = []
    closed: list[ClosedTrade] = []
    skipped: list[SkippedSignal] = []
    equity = config.starting_bank_usd
    reserved_margin = Decimal("0")
    peak_equity = equity
    max_drawdown = Decimal("0")
    max_drawdown_pct = Decimal("0")
    max_open = 0
    trade_id = 0

    timeline_at = ordered[0].entry_at
    occupancy_seconds = {count: 0.0 for count in range(len(config.slot_fractions) + 1)}
    slot_busy_seconds = {index: 0.0 for index in range(len(config.slot_fractions))}

    def advance_timeline(new_at: datetime) -> None:
        nonlocal timeline_at
        if new_at < timeline_at:
            raise ValueError("timeline moved backwards")
        seconds = (new_at - timeline_at).total_seconds()
        occupancy_seconds[len(open_by_slot)] += seconds
        for slot_index in open_by_slot:
            slot_busy_seconds[slot_index] += seconds
        timeline_at = new_at

    def close_until(cutoff: datetime) -> None:
        nonlocal equity, reserved_margin, peak_equity, max_drawdown, max_drawdown_pct
        while True:
            due = [trade for trade in open_by_slot.values() if trade.exit_at <= cutoff]
            if not due:
                return
            next_exit_at = min(trade.exit_at for trade in due)
            advance_timeline(next_exit_at)
            same_time = sorted(
                (trade for trade in due if trade.exit_at == next_exit_at),
                key=lambda trade: (trade.slot_index, trade.symbol, trade.trade_id),
            )
            for trade in same_time:
                reserved_margin -= trade.margin_usd
                equity += trade.gross_pnl_usd - trade.exit_fee_usd
                peak_equity = max(peak_equity, equity)
                drawdown = equity - peak_equity
                drawdown_pct = (
                    Decimal("0")
                    if peak_equity == 0
                    else drawdown / peak_equity * Decimal("100")
                )
                max_drawdown = min(max_drawdown, drawdown)
                max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)
                closed.append(
                    ClosedTrade(
                        trade_id=trade.trade_id,
                        policy_id=trade.policy_id,
                        symbol=trade.symbol,
                        entry_at=trade.entry_at,
                        exit_at=trade.exit_at,
                        outcome=trade.outcome,
                        move_pct=trade.move_pct,
                        slot_index=trade.slot_index,
                        slot_fraction=trade.slot_fraction,
                        margin_usd=trade.margin_usd,
                        notional_usd=trade.notional_usd,
                        entry_fee_usd=trade.entry_fee_usd,
                        exit_fee_usd=trade.exit_fee_usd,
                        gross_pnl_usd=trade.gross_pnl_usd,
                        net_pnl_usd=trade.net_pnl_usd,
                        equity_after_exit_usd=_money(equity),
                        old_exit_reason=trade.old_exit_reason,
                    )
                )
                del open_symbol_to_slot[trade.symbol]
                del open_by_slot[trade.slot_index]

    for signal in ordered:
        close_until(signal.entry_at)
        advance_timeline(signal.entry_at)
        if signal.symbol in open_symbol_to_slot:
            skipped.append(
                SkippedSignal(
                    policy_id,
                    signal.symbol,
                    signal.entry_at,
                    signal.exit_at,
                    "same_symbol_open",
                    signal.outcome,
                )
            )
            continue
        free_slots = [
            index for index in range(len(config.slot_fractions)) if index not in open_by_slot
        ]
        if not free_slots:
            skipped.append(
                SkippedSignal(
                    policy_id,
                    signal.symbol,
                    signal.entry_at,
                    signal.exit_at,
                    "no_capacity",
                    signal.outcome,
                )
            )
            continue

        window_start = signal.entry_at - timedelta(minutes=config.burst_window_minutes)
        recent_entries = [stamp for stamp in recent_entries if stamp > window_start]
        if use_burst_cap and len(recent_entries) >= config.burst_max_entries:
            skipped.append(
                SkippedSignal(
                    policy_id,
                    signal.symbol,
                    signal.entry_at,
                    signal.exit_at,
                    "burst_cap",
                    signal.outcome,
                )
            )
            continue

        slot_index = min(free_slots)
        sizing_bank = min(equity, config.starting_bank_usd)
        if sizing_bank <= 0:
            skipped.append(
                SkippedSignal(
                    policy_id,
                    signal.symbol,
                    signal.entry_at,
                    signal.exit_at,
                    "insufficient_margin",
                    signal.outcome,
                )
            )
            continue
        slot_budget = sizing_bank * config.slot_fractions[slot_index]
        available_budget = equity - reserved_margin
        if available_budget + Decimal("0.000001") < slot_budget:
            skipped.append(
                SkippedSignal(
                    policy_id,
                    signal.symbol,
                    signal.entry_at,
                    signal.exit_at,
                    "insufficient_margin",
                    signal.outcome,
                )
            )
            continue
        trade_id += 1
        trade = _make_trade(
            trade_id,
            policy_id,
            signal,
            slot_index,
            sizing_bank,
            config,
        )
        equity -= trade.entry_fee_usd
        reserved_margin += trade.margin_usd
        open_by_slot[slot_index] = trade
        open_symbol_to_slot[signal.symbol] = slot_index
        recent_entries.append(signal.entry_at)
        max_open = max(max_open, len(open_by_slot))

    last_exit = max((trade.exit_at for trade in open_by_slot.values()), default=timeline_at)
    close_until(last_exit)
    advance_timeline(last_exit)
    total_gross = sum((trade.gross_pnl_usd for trade in closed), Decimal("0"))
    total_fees = sum(
        (trade.entry_fee_usd + trade.exit_fee_usd for trade in closed), Decimal("0")
    )
    total_net = sum((trade.net_pnl_usd for trade in closed), Decimal("0"))
    return PolicyResult(
        policy_id=policy_id,
        executed=tuple(closed),
        skipped=tuple(skipped),
        starting_bank_usd=config.starting_bank_usd,
        ending_equity_usd=_money(equity),
        total_gross_pnl_usd=_money(total_gross),
        total_fees_usd=_money(total_fees),
        total_net_pnl_usd=_money(total_net),
        max_realized_drawdown_usd=_money(max_drawdown),
        max_realized_drawdown_pct=_money(max_drawdown_pct),
        max_open_positions=max_open,
        occupancy_seconds=occupancy_seconds,
        slot_busy_seconds=slot_busy_seconds,
        replay_start=ordered[0].entry_at,
        replay_end=last_exit,
    )


def _local_tz(config: ReplayConfig) -> timezone:
    return timezone(timedelta(hours=config.local_timezone_offset_hours))


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _daily_rows(result: PolicyResult, config: ReplayConfig) -> list[dict[str, Any]]:
    tz = _local_tz(config)
    entry_counts: dict[str, int] = {}
    exit_counts: dict[str, int] = {}
    net_by_exit: dict[str, Decimal] = {}
    net_by_entry: dict[str, Decimal] = {}
    fees_by_exit: dict[str, Decimal] = {}
    for trade in result.executed:
        entry_day = trade.entry_at.astimezone(tz).date().isoformat()
        exit_day = trade.exit_at.astimezone(tz).date().isoformat()
        entry_counts[entry_day] = entry_counts.get(entry_day, 0) + 1
        exit_counts[exit_day] = exit_counts.get(exit_day, 0) + 1
        net_by_entry[entry_day] = net_by_entry.get(entry_day, Decimal("0")) + trade.net_pnl_usd
        net_by_exit[exit_day] = net_by_exit.get(exit_day, Decimal("0")) + trade.net_pnl_usd
        fees_by_exit[exit_day] = fees_by_exit.get(exit_day, Decimal("0")) + (
            trade.entry_fee_usd + trade.exit_fee_usd
        )
    first_day = result.replay_start.astimezone(tz).date()
    last_entry_day = max(trade.entry_at.astimezone(tz).date() for trade in result.executed)
    days: list[str] = []
    current = first_day
    while current <= last_entry_day:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return [
        {
            "policy_id": result.policy_id,
            "local_date": day,
            "entries": entry_counts.get(day, 0),
            "exits": exit_counts.get(day, 0),
            "net_pnl_by_entry_day_usd": _money(net_by_entry.get(day, Decimal("0"))),
            "realized_net_pnl_by_exit_day_usd": _money(net_by_exit.get(day, Decimal("0"))),
            "fees_by_exit_day_usd": _money(fees_by_exit.get(day, Decimal("0"))),
        }
        for day in days
    ]

def _trade_counts_by_outcome(trades: Iterable[ClosedTrade]) -> dict[str, int]:
    result: dict[str, int] = {}
    for trade in trades:
        result[trade.outcome] = result.get(trade.outcome, 0) + 1
    return result


def _skip_counts(skipped: Iterable[SkippedSignal]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in skipped:
        result[item.reason] = result.get(item.reason, 0) + 1
    return result


def _stop_cluster_rows(result: PolicyResult, window_minutes: int = 15) -> list[dict[str, Any]]:
    stops = sorted(
        trade.exit_at
        for trade in result.executed
        if trade.outcome != "reached_1p10"
    )
    rows: list[dict[str, Any]] = []
    cluster: list[datetime] = []
    for stamp in stops:
        if not cluster or stamp - cluster[-1] <= timedelta(minutes=window_minutes):
            cluster.append(stamp)
            continue
        if len(cluster) >= 2:
            rows.append(
                {
                    "policy_id": result.policy_id,
                    "window_minutes": window_minutes,
                    "cluster_start": cluster[0].isoformat(),
                    "cluster_end": cluster[-1].isoformat(),
                    "stop_count": len(cluster),
                }
            )
        cluster = [stamp]
    if len(cluster) >= 2:
        rows.append(
            {
                "policy_id": result.policy_id,
                "window_minutes": window_minutes,
                "cluster_start": cluster[0].isoformat(),
                "cluster_end": cluster[-1].isoformat(),
                "stop_count": len(cluster),
            }
        )
    return rows


def _policy_summary_row(result: PolicyResult, config: ReplayConfig) -> dict[str, Any]:
    daily = _daily_rows(result, config)
    entry_counts = [int(row["entries"]) for row in daily]
    realized = [float(row["realized_net_pnl_by_exit_day_usd"]) for row in daily]
    outcome_counts = _trade_counts_by_outcome(result.executed)
    skipped_counts = _skip_counts(result.skipped)
    duration_seconds = max(0.0, (result.replay_end - result.replay_start).total_seconds())
    occupied_two_or_more = sum(
        seconds for count, seconds in result.occupancy_seconds.items() if count >= 2
    )
    clusters = _stop_cluster_rows(result)
    return {
        "policy_id": result.policy_id,
        "signals_considered": len(result.executed) + len(result.skipped),
        "calendar_days": len(daily),
        "executed_trades": len(result.executed),
        "executed_per_calendar_day_mean": (
            statistics.mean(entry_counts) if entry_counts else None
        ),
        "executed_per_calendar_day_median": _median([float(value) for value in entry_counts]),
        "days_0_entries": sum(value == 0 for value in entry_counts),
        "days_1_3_entries": sum(1 <= value <= 3 for value in entry_counts),
        "days_4_7_entries": sum(4 <= value <= 7 for value in entry_counts),
        "days_8_12_entries": sum(8 <= value <= 12 for value in entry_counts),
        "days_13plus_entries": sum(value >= 13 for value in entry_counts),
        "profit_1p10": outcome_counts.get("reached_1p10", 0),
        "stop_minus_1": outcome_counts.get("baseline_initial_stop", 0)
        + outcome_counts.get("initial_stop_before_0p50", 0),
        "stop_minus_0p50": outcome_counts.get("floor_minus_0p50", 0),
        "skip_same_symbol": skipped_counts.get("same_symbol_open", 0),
        "skip_no_capacity": skipped_counts.get("no_capacity", 0),
        "skip_burst_cap": skipped_counts.get("burst_cap", 0),
        "skip_insufficient_margin": skipped_counts.get("insufficient_margin", 0),
        "gross_pnl_usd_capped_slots": result.total_gross_pnl_usd,
        "fees_usd_capped_slots": result.total_fees_usd,
        "net_pnl_usd_capped_slots": result.total_net_pnl_usd,
        "ledger_end_usd_no_upsize": result.ending_equity_usd,
        "max_realized_drawdown_usd": result.max_realized_drawdown_usd,
        "max_realized_drawdown_pct": result.max_realized_drawdown_pct,
        "max_open_positions": result.max_open_positions,
        "time_with_2plus_positions_pct": (
            0.0 if duration_seconds == 0 else occupied_two_or_more / duration_seconds * 100.0
        ),
        "stop_clusters_15m_ge2": len(clusters),
        "max_stops_in_15m_cluster": max(
            (int(row["stop_count"]) for row in clusters),
            default=1,
        ),
        "best_realized_day_usd": max(realized) if realized else None,
        "median_realized_day_usd": _median(realized),
        "worst_realized_day_usd": min(realized) if realized else None,
    }

def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _serialize_trade(trade: ClosedTrade) -> dict[str, Any]:
    row = asdict(trade)
    row["entry_at"] = trade.entry_at.isoformat()
    row["exit_at"] = trade.exit_at.isoformat()
    return row


def _serialize_skip(item: SkippedSignal) -> dict[str, Any]:
    row = asdict(item)
    row["entry_at"] = item.entry_at.isoformat()
    row["exit_at"] = item.exit_at.isoformat() if item.exit_at else ""
    return row


def _occupancy_rows(result: PolicyResult) -> list[dict[str, Any]]:
    duration_seconds = max(0.0, (result.replay_end - result.replay_start).total_seconds())
    rows: list[dict[str, Any]] = []
    for count in sorted(result.occupancy_seconds):
        seconds = result.occupancy_seconds[count]
        rows.append(
            {
                "policy_id": result.policy_id,
                "metric": f"open_positions_{count}",
                "hours": seconds / 3600.0,
                "share_pct": 0.0 if duration_seconds == 0 else seconds / duration_seconds * 100.0,
            }
        )
    for slot_index, seconds in sorted(result.slot_busy_seconds.items()):
        rows.append(
            {
                "policy_id": result.policy_id,
                "metric": f"slot_{slot_index + 1}_busy",
                "hours": seconds / 3600.0,
                "share_pct": 0.0 if duration_seconds == 0 else seconds / duration_seconds * 100.0,
            }
        )
    return rows


def _summary_markdown(
    rows: list[dict[str, Any]],
    config: ReplayConfig,
    cutoff: datetime,
) -> str:
    lines = [
        "# P47L chronological portfolio replay",
        "",
        "Capped reference-bank benchmark; no upside compounding or runner beyond +1.10%.",
        "",
        f"- Reference bank: ${config.starting_bank_usd}",
        f"- Leverage: {config.leverage}x",
        "- Slots: " + "/".join(f"{value * 100:.0f}%" for value in config.slot_fractions),
        f"- Maker fee: {config.maker_fee_rate * 100}%",
        f"- Taker fee: {config.taker_fee_rate * 100}%",
        "- Successful exit: +1.10%, maker fee",
        "- Stops: -1.00% or -0.50%, taker fee",
        f"- Complete-entry cutoff: {cutoff.isoformat()}",
        "",
        "## Policies",
        "",
        "| policy | executed | /day | +1.10 | -1 | -0.5 | skipped symbol | "
        "skipped full | burst skip | margin skip | net USD | worst day | max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {policy_id} | {executed_trades} | {executed_per_calendar_day_mean:.2f} | "
            "{profit_1p10} | {stop_minus_1} | {stop_minus_0p50} | {skip_same_symbol} | "
            "{skip_no_capacity} | {skip_burst_cap} | {skip_insufficient_margin} | "
            "{net_pnl_usd_capped_slots} | {worst_realized_day_usd:.2f} | "
            "{max_realized_drawdown_pct}% |".format(**row)
        )
    lines.extend(["", "## Daily Entry distribution", ""])
    for row in rows:
        lines.append(
            "- {policy_id}: 0={days_0_entries}, 1-3={days_1_3_entries}, "
            "4-7={days_4_7_entries}, 8-12={days_8_12_entries}, "
            "13+={days_13plus_entries}; median={executed_per_calendar_day_median:.1f}/day".format(
                **row
            )
        )
    lines.extend(
        [
            "",
            "## Guardrails / interpretation",
            "",
            "- This is a chronological portfolio replay, not a retune of frozen Entry V1 or P46.",
            "- Sizing is capped at the $100 reference bank and scales down after losses; it never "
            "scales above $100 during this historical replay.",
            "- Slot budgets include maker entry fee reserve; target budgets are 50/30/20%.",
            "- Net USD is intraday/portfolio economics, not a 90-day compounding forecast.",
            "- Same-symbol signals are rejected while that symbol's prior portfolio trade is open.",
            "- A fourth simultaneous signal is rejected when all three slots are occupied.",
            "- CAP2_15M is a sensitivity only: at most two new portfolio entries per "
            "rolling 15 minutes.",
            "- P47K data-end tail is excluded from new entries using the earliest censored "
            "Entry cutoff.",
            "- Realized drawdown ignores mark-to-market drawdown of still-open positions.",
            "- Exit fee values use entry notional as the fee base; this is a small approximation.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(root: Path, output_dir: Path, config: ReplayConfig) -> dict[str, Any]:
    signals, cutoff, sources = load_signal_events(root)
    policies = (
        replay_policy(signals, config, policy_id="NO_CAP_50_30_20", use_burst_cap=False),
        replay_policy(signals, config, policy_id="CAP2_15M_50_30_20", use_burst_cap=True),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    policy_rows = [_policy_summary_row(result, config) for result in policies]
    _write_csv(output_dir / "policy_summary.csv", policy_rows)
    _write_csv(
        output_dir / "executed_trades.csv",
        [_serialize_trade(trade) for result in policies for trade in result.executed],
    )
    _write_csv(
        output_dir / "skipped_signals.csv",
        [_serialize_skip(item) for result in policies for item in result.skipped],
    )
    _write_csv(
        output_dir / "daily_summary.csv",
        [row for result in policies for row in _daily_rows(result, config)],
    )
    _write_csv(
        output_dir / "occupancy_summary.csv",
        [row for result in policies for row in _occupancy_rows(result)],
    )
    _write_csv(
        output_dir / "stop_clusters_15m.csv",
        [row for result in policies for row in _stop_cluster_rows(result)],
    )
    _write_csv(output_dir / "sources.csv", [asdict(source) for source in sources])
    summary = {
        "research_version": "P47L portfolio_replay_v25",
        "created_at": datetime.now(UTC).isoformat(),
        "input_signal_count_complete_before_cutoff": len(signals),
        "censored_entry_cutoff": cutoff.isoformat(),
        "config": {
            "starting_bank_usd": str(config.starting_bank_usd),
            "leverage": str(config.leverage),
            "slot_fractions": [str(value) for value in config.slot_fractions],
            "maker_fee_rate": str(config.maker_fee_rate),
            "taker_fee_rate": str(config.taker_fee_rate),
            "local_timezone_offset_hours": config.local_timezone_offset_hours,
            "burst_window_minutes": config.burst_window_minutes,
            "burst_max_entries": config.burst_max_entries,
            "pnl_contract": "+1.10 maker / -1.00 taker / -0.50 taker",
            "position_sizing": (
                "capped $100 reference bank; 50/30/20 slot budgets; "
                "scale down after losses, never upsize above $100"
            ),
        },
        "sources": [asdict(source) for source in sources],
        "policies": policy_rows,
        "notes": [
            "Frozen Entry V1 unchanged; P46 unchanged.",
            "Uses compact P47K and P47G reports only; no market-data download or raw replay.",
            "Signal-level P47K outcomes are replayed chronologically with portfolio occupancy.",
            "Finite available margin is enforced; unavailable target slot budget is skipped.",
            "Realized drawdown excludes mark-to-market drawdown of open positions.",
            "CAP2_15M is diagnostic sensitivity, not a production rule or tuned winner.",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(
        _summary_markdown(policy_rows, config, cutoff),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P47L chronological fixed-stake portfolio replay")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--starting-bank-usd", type=Decimal, default=Decimal("100"))
    parser.add_argument("--leverage", type=Decimal, default=Decimal("10"))
    parser.add_argument("--maker-fee-rate", type=Decimal, default=Decimal("0.00020"))
    parser.add_argument("--taker-fee-rate", type=Decimal, default=Decimal("0.00055"))
    parser.add_argument("--timezone-offset-hours", type=int, default=5)
    parser.add_argument("--burst-window-minutes", type=int, default=15)
    parser.add_argument("--burst-max-entries", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "portfolio_replay_v1" / f"ALL9_{stamp}"
    config = ReplayConfig(
        starting_bank_usd=args.starting_bank_usd,
        leverage=args.leverage,
        maker_fee_rate=args.maker_fee_rate,
        taker_fee_rate=args.taker_fee_rate,
        local_timezone_offset_hours=args.timezone_offset_hours,
        burst_window_minutes=args.burst_window_minutes,
        burst_max_entries=args.burst_max_entries,
    )
    output_dir = output_dir.resolve()
    summary = run(root, output_dir, config)
    archive_path = Path(
        shutil.make_archive(
            str(output_dir),
            "zip",
            root_dir=output_dir.parent,
            base_dir=output_dir.name,
        )
    )
    print("P47L chronological portfolio replay complete")
    for row in summary["policies"]:
        print(
            f"  {row['policy_id']}: executed={row['executed_trades']} "
            f"mean/day={row['executed_per_calendar_day_mean']:.2f} "
            f"net=${row['net_pnl_usd_capped_slots']} "
            f"worst_day=${row['worst_realized_day_usd']:.2f} "
            f"max_open={row['max_open_positions']}"
        )
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    print(f"Compact archive: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
