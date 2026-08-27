from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from bybit_workbench.research.portfolio_replay_v25 import SignalEvent, load_signal_events


@dataclass(frozen=True, slots=True)
class SnowballConfig:
    starting_bank_usd: Decimal = Decimal("100")
    leverage: Decimal = Decimal("10")
    allocation_fraction: Decimal = Decimal("0.50")
    maker_fee_rate: Decimal = Decimal("0.00020")
    taker_fee_rate: Decimal = Decimal("0.00055")
    minimum_allocation_usd: Decimal = Decimal("0")
    local_timezone_offset_hours: int = 5

    def __post_init__(self) -> None:
        if self.starting_bank_usd <= 0:
            raise ValueError("starting_bank_usd must be positive")
        if self.leverage <= 0:
            raise ValueError("leverage must be positive")
        if not Decimal("0") < self.allocation_fraction < Decimal("1"):
            raise ValueError("allocation_fraction must be between 0 and 1")
        if self.maker_fee_rate < 0 or self.taker_fee_rate < 0:
            raise ValueError("fee rates may not be negative")
        if self.minimum_allocation_usd < 0:
            raise ValueError("minimum_allocation_usd may not be negative")
        if not -23 <= self.local_timezone_offset_hours <= 23:
            raise ValueError("invalid timezone offset")


@dataclass(frozen=True, slots=True)
class OpenSnowballTrade:
    trade_id: int
    policy_id: str
    symbol: str
    entry_at: datetime
    exit_at: datetime
    outcome: str
    move_pct: Decimal
    open_positions_before: int
    available_before_usd: Decimal
    allocation_budget_usd: Decimal
    margin_usd: Decimal
    notional_usd: Decimal
    entry_fee_usd: Decimal
    exit_fee_usd: Decimal
    gross_pnl_usd: Decimal
    net_pnl_usd: Decimal


@dataclass(frozen=True, slots=True)
class ClosedSnowballTrade:
    trade_id: int
    policy_id: str
    symbol: str
    entry_at: datetime
    exit_at: datetime
    outcome: str
    move_pct: Decimal
    open_positions_before: int
    available_before_usd: Decimal
    allocation_budget_usd: Decimal
    margin_usd: Decimal
    notional_usd: Decimal
    entry_fee_usd: Decimal
    exit_fee_usd: Decimal
    gross_pnl_usd: Decimal
    net_pnl_usd: Decimal
    wallet_after_exit_usd: Decimal
    available_after_exit_usd: Decimal


@dataclass(frozen=True, slots=True)
class SkippedSnowballSignal:
    policy_id: str
    symbol: str
    entry_at: datetime
    exit_at: datetime
    outcome: str
    reason: str
    available_before_usd: Decimal
    proposed_allocation_usd: Decimal


@dataclass(frozen=True, slots=True)
class SnowballResult:
    policy_id: str
    executed: tuple[ClosedSnowballTrade, ...]
    skipped: tuple[SkippedSnowballSignal, ...]
    starting_bank_usd: Decimal
    ending_wallet_usd: Decimal
    total_gross_pnl_usd: Decimal
    total_fees_usd: Decimal
    total_net_pnl_usd: Decimal
    max_realized_drawdown_usd: Decimal
    max_realized_drawdown_pct: Decimal
    max_open_positions: int
    occupancy_seconds: dict[int, float]
    replay_start: datetime
    replay_end: datetime


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def _exit_fee_rate(signal: SignalEvent, config: SnowballConfig) -> Decimal:
    if signal.outcome == "reached_1p10":
        return config.maker_fee_rate
    return config.taker_fee_rate


def _build_trade(
    trade_id: int,
    policy_id: str,
    signal: SignalEvent,
    *,
    open_positions_before: int,
    available_before_usd: Decimal,
    allocation_budget_usd: Decimal,
    config: SnowballConfig,
) -> OpenSnowballTrade:
    fee_multiplier = Decimal("1") + config.leverage * config.maker_fee_rate
    margin = allocation_budget_usd / fee_multiplier
    notional = margin * config.leverage
    entry_fee = _money(notional * config.maker_fee_rate)
    exit_fee = _money(notional * _exit_fee_rate(signal, config))
    gross = _money(notional * signal.move_pct / Decimal("100"))
    net = _money(gross - entry_fee - exit_fee)
    return OpenSnowballTrade(
        trade_id=trade_id,
        policy_id=policy_id,
        symbol=signal.symbol,
        entry_at=signal.entry_at,
        exit_at=signal.exit_at,
        outcome=signal.outcome,
        move_pct=signal.move_pct,
        open_positions_before=open_positions_before,
        available_before_usd=_money(available_before_usd),
        allocation_budget_usd=_money(allocation_budget_usd),
        margin_usd=_money(margin),
        notional_usd=_money(notional),
        entry_fee_usd=entry_fee,
        exit_fee_usd=exit_fee,
        gross_pnl_usd=gross,
        net_pnl_usd=net,
    )


def replay_snowball(
    signals: Iterable[SignalEvent],
    config: SnowballConfig,
    *,
    policy_id: str,
) -> SnowballResult:
    ordered = tuple(sorted(signals, key=lambda item: (item.entry_at, item.symbol)))
    if not ordered:
        raise ValueError("signals may not be empty")

    open_trades: dict[int, OpenSnowballTrade] = {}
    closed: list[ClosedSnowballTrade] = []
    skipped: list[SkippedSnowballSignal] = []
    wallet = config.starting_bank_usd
    reserved_margin = Decimal("0")
    peak_wallet = wallet
    max_drawdown = Decimal("0")
    max_drawdown_pct = Decimal("0")
    max_open = 0
    trade_id = 0

    timeline_at = ordered[0].entry_at
    occupancy_seconds: dict[int, float] = {0: 0.0}

    def advance_timeline(new_at: datetime) -> None:
        nonlocal timeline_at
        if new_at < timeline_at:
            raise ValueError("timeline moved backwards")
        seconds = (new_at - timeline_at).total_seconds()
        count = len(open_trades)
        occupancy_seconds[count] = occupancy_seconds.get(count, 0.0) + seconds
        timeline_at = new_at

    def update_drawdown() -> None:
        nonlocal peak_wallet, max_drawdown, max_drawdown_pct
        peak_wallet = max(peak_wallet, wallet)
        drawdown = wallet - peak_wallet
        drawdown_pct = (
            Decimal("0")
            if peak_wallet == 0
            else drawdown / peak_wallet * Decimal("100")
        )
        max_drawdown = min(max_drawdown, drawdown)
        max_drawdown_pct = min(max_drawdown_pct, drawdown_pct)

    def close_until(cutoff: datetime) -> None:
        nonlocal wallet, reserved_margin
        while True:
            due = [trade for trade in open_trades.values() if trade.exit_at <= cutoff]
            if not due:
                return
            next_exit_at = min(trade.exit_at for trade in due)
            advance_timeline(next_exit_at)
            same_time = sorted(
                (trade for trade in due if trade.exit_at == next_exit_at),
                key=lambda trade: (trade.symbol, trade.trade_id),
            )
            for trade in same_time:
                reserved_margin -= trade.margin_usd
                wallet += trade.gross_pnl_usd - trade.exit_fee_usd
                update_drawdown()
                closed.append(
                    ClosedSnowballTrade(
                        trade_id=trade.trade_id,
                        policy_id=trade.policy_id,
                        symbol=trade.symbol,
                        entry_at=trade.entry_at,
                        exit_at=trade.exit_at,
                        outcome=trade.outcome,
                        move_pct=trade.move_pct,
                        open_positions_before=trade.open_positions_before,
                        available_before_usd=trade.available_before_usd,
                        allocation_budget_usd=trade.allocation_budget_usd,
                        margin_usd=trade.margin_usd,
                        notional_usd=trade.notional_usd,
                        entry_fee_usd=trade.entry_fee_usd,
                        exit_fee_usd=trade.exit_fee_usd,
                        gross_pnl_usd=trade.gross_pnl_usd,
                        net_pnl_usd=trade.net_pnl_usd,
                        wallet_after_exit_usd=_money(wallet),
                        available_after_exit_usd=_money(wallet - reserved_margin),
                    )
                )
                del open_trades[trade.trade_id]

    for signal in ordered:
        close_until(signal.entry_at)
        advance_timeline(signal.entry_at)
        available = wallet - reserved_margin
        if available <= 0:
            skipped.append(
                SkippedSnowballSignal(
                    policy_id=policy_id,
                    symbol=signal.symbol,
                    entry_at=signal.entry_at,
                    exit_at=signal.exit_at,
                    outcome=signal.outcome,
                    reason="no_available_deposit",
                    available_before_usd=_money(available),
                    proposed_allocation_usd=Decimal("0"),
                )
            )
            continue
        allocation_budget = available * config.allocation_fraction
        if allocation_budget + Decimal("0.000001") < config.minimum_allocation_usd:
            skipped.append(
                SkippedSnowballSignal(
                    policy_id=policy_id,
                    symbol=signal.symbol,
                    entry_at=signal.entry_at,
                    exit_at=signal.exit_at,
                    outcome=signal.outcome,
                    reason="below_minimum_allocation",
                    available_before_usd=_money(available),
                    proposed_allocation_usd=_money(allocation_budget),
                )
            )
            continue
        trade_id += 1
        trade = _build_trade(
            trade_id,
            policy_id,
            signal,
            open_positions_before=len(open_trades),
            available_before_usd=available,
            allocation_budget_usd=allocation_budget,
            config=config,
        )
        wallet -= trade.entry_fee_usd
        update_drawdown()
        reserved_margin += trade.margin_usd
        open_trades[trade.trade_id] = trade
        max_open = max(max_open, len(open_trades))

    last_exit = max((trade.exit_at for trade in open_trades.values()), default=timeline_at)
    close_until(last_exit)
    advance_timeline(last_exit)
    total_gross = sum((trade.gross_pnl_usd for trade in closed), Decimal("0"))
    total_fees = sum(
        (trade.entry_fee_usd + trade.exit_fee_usd for trade in closed), Decimal("0")
    )
    total_net = sum((trade.net_pnl_usd for trade in closed), Decimal("0"))
    return SnowballResult(
        policy_id=policy_id,
        executed=tuple(closed),
        skipped=tuple(skipped),
        starting_bank_usd=config.starting_bank_usd,
        ending_wallet_usd=_money(wallet),
        total_gross_pnl_usd=_money(total_gross),
        total_fees_usd=_money(total_fees),
        total_net_pnl_usd=_money(total_net),
        max_realized_drawdown_usd=_money(max_drawdown),
        max_realized_drawdown_pct=_money(max_drawdown_pct),
        max_open_positions=max_open,
        occupancy_seconds=occupancy_seconds,
        replay_start=ordered[0].entry_at,
        replay_end=last_exit,
    )


def _local_tz(config: SnowballConfig) -> timezone:
    return timezone(timedelta(hours=config.local_timezone_offset_hours))


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _outcome_counts(trades: Iterable[ClosedSnowballTrade]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for trade in trades:
        counts[trade.outcome] = counts.get(trade.outcome, 0) + 1
    return counts


def _daily_rows(result: SnowballResult, config: SnowballConfig) -> list[dict[str, Any]]:
    tz = _local_tz(config)
    entry_counts: dict[str, int] = {}
    realized_net: dict[str, Decimal] = {}
    fees: dict[str, Decimal] = {}
    for trade in result.executed:
        entry_day = trade.entry_at.astimezone(tz).date().isoformat()
        exit_day = trade.exit_at.astimezone(tz).date().isoformat()
        entry_counts[entry_day] = entry_counts.get(entry_day, 0) + 1
        realized_net[exit_day] = realized_net.get(exit_day, Decimal("0")) + trade.net_pnl_usd
        fees[exit_day] = fees.get(exit_day, Decimal("0")) + trade.entry_fee_usd + trade.exit_fee_usd
    first_day = result.replay_start.astimezone(tz).date()
    last_day = result.replay_end.astimezone(tz).date()
    rows: list[dict[str, Any]] = []
    current = first_day
    while current <= last_day:
        day = current.isoformat()
        rows.append(
            {
                "policy_id": result.policy_id,
                "local_date": day,
                "entries": entry_counts.get(day, 0),
                "realized_net_pnl_usd": _money(realized_net.get(day, Decimal("0"))),
                "fees_usd": _money(fees.get(day, Decimal("0"))),
            }
        )
        current += timedelta(days=1)
    return rows


def _policy_summary(result: SnowballResult, config: SnowballConfig) -> dict[str, Any]:
    daily = _daily_rows(result, config)
    entry_counts = [int(row["entries"]) for row in daily]
    realized = [float(row["realized_net_pnl_usd"]) for row in daily]
    allocations = [float(trade.allocation_budget_usd) for trade in result.executed]
    notionals = [float(trade.notional_usd) for trade in result.executed]
    outcomes = _outcome_counts(result.executed)
    skip_counts: dict[str, int] = {}
    for item in result.skipped:
        skip_counts[item.reason] = skip_counts.get(item.reason, 0) + 1
    return {
        "policy_id": result.policy_id,
        "signals_considered": len(result.executed) + len(result.skipped),
        "executed_trades": len(result.executed),
        "skipped_trades": len(result.skipped),
        "skip_below_minimum": skip_counts.get("below_minimum_allocation", 0),
        "skip_no_available_deposit": skip_counts.get("no_available_deposit", 0),
        "max_open_positions": result.max_open_positions,
        "mean_entries_per_day": statistics.mean(entry_counts) if entry_counts else None,
        "median_entries_per_day": _median([float(value) for value in entry_counts]),
        "median_allocation_usd": _median(allocations),
        "min_allocation_usd": min(allocations) if allocations else None,
        "max_allocation_usd": max(allocations) if allocations else None,
        "median_notional_usd": _median(notionals),
        "profit_1p10": outcomes.get("reached_1p10", 0),
        "stop_minus_1": outcomes.get("baseline_initial_stop", 0)
        + outcomes.get("initial_stop_before_0p50", 0),
        "stop_minus_0p50": outcomes.get("floor_minus_0p50", 0),
        "starting_bank_usd": result.starting_bank_usd,
        "ending_wallet_usd": result.ending_wallet_usd,
        "total_gross_pnl_usd": result.total_gross_pnl_usd,
        "total_fees_usd": result.total_fees_usd,
        "total_net_pnl_usd": result.total_net_pnl_usd,
        "max_realized_drawdown_usd": result.max_realized_drawdown_usd,
        "max_realized_drawdown_pct": result.max_realized_drawdown_pct,
        "best_realized_day_usd": max(realized) if realized else None,
        "median_realized_day_usd": _median(realized),
        "worst_realized_day_usd": min(realized) if realized else None,
    }


def _serialize_trade(trade: ClosedSnowballTrade) -> dict[str, Any]:
    row = asdict(trade)
    row["entry_at"] = trade.entry_at.isoformat()
    row["exit_at"] = trade.exit_at.isoformat()
    return row


def _serialize_skip(item: SkippedSnowballSignal) -> dict[str, Any]:
    row = asdict(item)
    row["entry_at"] = item.entry_at.isoformat()
    row["exit_at"] = item.exit_at.isoformat()
    return row


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _occupancy_rows(result: SnowballResult) -> list[dict[str, Any]]:
    duration = max(0.0, (result.replay_end - result.replay_start).total_seconds())
    return [
        {
            "policy_id": result.policy_id,
            "open_positions": count,
            "hours": seconds / 3600.0,
            "share_pct": 0.0 if duration == 0 else seconds / duration * 100.0,
        }
        for count, seconds in sorted(result.occupancy_seconds.items())
    ]


def _summary_markdown(
    rows: list[dict[str, Any]],
    configs: list[SnowballConfig],
    cutoff: datetime,
) -> str:
    lines = [
        "# P47M dynamic 50%-of-free-deposit snowball replay",
        "",
        "Research-only capacity/economics replay. Every complete signal is eligible, including",
        "same-symbol overlaps. No fixed slot count and no one-position-per-symbol rule.",
        "",
        f"- Starting bank: ${configs[0].starting_bank_usd}",
        f"- Leverage: {configs[0].leverage}x",
        "- Each new signal takes "
        f"{configs[0].allocation_fraction * 100}% of currently free deposit",
        f"- Maker fee: {configs[0].maker_fee_rate * 100}%",
        f"- Taker fee: {configs[0].taker_fee_rate * 100}%",
        "- Successful exit: +1.10% (maker); stops: -1.00% or -0.50% (taker)",
        f"- Complete-entry cutoff: {cutoff.isoformat()}",
        "",
        "## Policies",
        "",
        "| policy | executed | skipped | max open | median alloc | net USD | end wallet | max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {policy_id} | {executed_trades} | {skipped_trades} | {max_open_positions} | "
            "${median_allocation_usd:.2f} | ${total_net_pnl_usd} | ${ending_wallet_usd} | "
            "{max_realized_drawdown_pct}% |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            "- This intentionally ignores portfolio slot caps and same-symbol occupancy.",
            "- Same-symbol overlapping signals are virtual independent research positions; "
            "an actual",
            "  Bybit one-way account cannot necessarily execute them as separate positions.",
            "- HALF_FREE_NO_MIN tests the pure mathematical snowball.",
            "- HALF_FREE_MIN6 treats $6 as a practical allocation-floor sensitivity only; "
            "it is NOT",
            "  asserted to be the Bybit contract minimum for every symbol.",
            "- Free deposit = wallet balance minus margin reserved by still-open virtual "
            "positions.",
            "- Entry fee is reserved inside each 50% allocation budget, so an untouched $100 bank",
            "  produces approximately $50, $25, $12.50, $6.25, $3.125... overlapping budgets.",
            "- When any position exits, its reserved margin becomes free immediately and the next",
            "  signal again receives 50% of the then-current free deposit, including realized PnL.",
            "- This is continuous historical bookkeeping and not a forecast of future account "
            "growth.",
            "- Realized drawdown excludes mark-to-market drawdown of still-open positions.",
            "- Frozen Entry V1 and P46 are not modified or retuned.",
        ]
    )
    return "\n".join(lines) + "\n"


def run(root: Path, output_dir: Path, config: SnowballConfig) -> dict[str, Any]:
    signals, cutoff, sources = load_signal_events(root)
    no_min = SnowballConfig(
        starting_bank_usd=config.starting_bank_usd,
        leverage=config.leverage,
        allocation_fraction=config.allocation_fraction,
        maker_fee_rate=config.maker_fee_rate,
        taker_fee_rate=config.taker_fee_rate,
        minimum_allocation_usd=Decimal("0"),
        local_timezone_offset_hours=config.local_timezone_offset_hours,
    )
    min_six = SnowballConfig(
        starting_bank_usd=config.starting_bank_usd,
        leverage=config.leverage,
        allocation_fraction=config.allocation_fraction,
        maker_fee_rate=config.maker_fee_rate,
        taker_fee_rate=config.taker_fee_rate,
        minimum_allocation_usd=config.minimum_allocation_usd,
        local_timezone_offset_hours=config.local_timezone_offset_hours,
    )
    policies = (
        (no_min, replay_snowball(signals, no_min, policy_id="HALF_FREE_NO_MIN")),
        (min_six, replay_snowball(signals, min_six, policy_id="HALF_FREE_MIN6")),
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    policy_rows = [_policy_summary(result, policy_config) for policy_config, result in policies]
    _write_csv(output_dir / "policy_summary.csv", policy_rows)
    _write_csv(
        output_dir / "trades.csv",
        [_serialize_trade(trade) for _, result in policies for trade in result.executed],
    )
    _write_csv(
        output_dir / "skipped_signals.csv",
        [_serialize_skip(item) for _, result in policies for item in result.skipped],
    )
    _write_csv(
        output_dir / "daily_summary.csv",
        [
            row
            for policy_config, result in policies
            for row in _daily_rows(result, policy_config)
        ],
    )
    _write_csv(
        output_dir / "occupancy_summary.csv",
        [row for _, result in policies for row in _occupancy_rows(result)],
    )
    _write_csv(output_dir / "sources.csv", [asdict(source) for source in sources])
    summary = {
        "research_version": "P47M snowball_allocation_v26",
        "created_at": datetime.now(UTC).isoformat(),
        "input_signal_count_complete_before_cutoff": len(signals),
        "censored_entry_cutoff": cutoff.isoformat(),
        "base_config": {
            "starting_bank_usd": str(config.starting_bank_usd),
            "leverage": str(config.leverage),
            "allocation_fraction": str(config.allocation_fraction),
            "maker_fee_rate": str(config.maker_fee_rate),
            "taker_fee_rate": str(config.taker_fee_rate),
            "minimum_allocation_usd_sensitivity": str(config.minimum_allocation_usd),
            "local_timezone_offset_hours": config.local_timezone_offset_hours,
            "pnl_contract": "+1.10 maker / -1.00 taker / -0.50 taker",
        },
        "sources": [asdict(source) for source in sources],
        "policies": policy_rows,
        "notes": [
            "Every complete signal is eligible; no fixed capacity and no same-symbol rejection.",
            "Free deposit is dynamic: wallet minus reserved margin.",
            "Each accepted signal allocates 50% of free deposit including entry-fee reserve.",
            "$6 floor is a research sensitivity, not an exchange contract specification.",
            "Same-symbol overlaps are virtual and may not map to one-way Bybit execution.",
            "Frozen Entry V1 and P46 unchanged.",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    (output_dir / "summary.md").write_text(
        _summary_markdown(policy_rows, [no_min, min_six], cutoff),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P47M dynamic half-free-deposit replay")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--starting-bank-usd", type=Decimal, default=Decimal("100"))
    parser.add_argument("--leverage", type=Decimal, default=Decimal("10"))
    parser.add_argument("--allocation-fraction", type=Decimal, default=Decimal("0.50"))
    parser.add_argument("--maker-fee-rate", type=Decimal, default=Decimal("0.00020"))
    parser.add_argument("--taker-fee-rate", type=Decimal, default=Decimal("0.00055"))
    parser.add_argument("--minimum-allocation-usd", type=Decimal, default=Decimal("6"))
    parser.add_argument("--timezone-offset-hours", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "snowball_allocation_v1" / f"ALL9_{stamp}"
    config = SnowballConfig(
        starting_bank_usd=args.starting_bank_usd,
        leverage=args.leverage,
        allocation_fraction=args.allocation_fraction,
        maker_fee_rate=args.maker_fee_rate,
        taker_fee_rate=args.taker_fee_rate,
        minimum_allocation_usd=args.minimum_allocation_usd,
        local_timezone_offset_hours=args.timezone_offset_hours,
    )
    summary = run(root, output_dir.resolve(), config)
    archive_path = Path(
        shutil.make_archive(
            str(output_dir.resolve()),
            "zip",
            root_dir=output_dir.resolve().parent,
            base_dir=output_dir.resolve().name,
        )
    )
    print("P47M dynamic half-free-deposit replay complete")
    for row in summary["policies"]:
        print(
            f"  {row['policy_id']}: executed={row['executed_trades']} "
            f"skipped={row['skipped_trades']} max_open={row['max_open_positions']} "
            f"median_alloc=${row['median_allocation_usd']:.2f} "
            f"net=${row['total_net_pnl_usd']} end=${row['ending_wallet_usd']}"
        )
    print(f"Report: {output_dir.resolve() / 'summary.json'}")
    print(f"Readable summary: {output_dir.resolve() / 'summary.md'}")
    print(f"Compact archive: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
