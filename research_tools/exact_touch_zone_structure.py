from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from bybit_workbench.research.exact_touch_pre_stop_mfe import (
    Signal,
    archive_map,
    days_for,
    load_signals,
)
from bybit_workbench.research.exit_break_even_v13 import (
    TradeDayCache,
    directional_move_pct,
)

State = Literal[
    "protective_hold_reclaim",
    "protective_clean_break_against",
    "obstacle_rejection_against",
    "obstacle_clean_break_with",
]
Outcome = Literal["target_first", "stop_first", "unresolved_24h"]


@dataclass(frozen=True, slots=True)
class ZoneEvent:
    symbol: str
    role: str
    event_at: datetime
    outcome: str
    outcome_at: datetime


@dataclass(frozen=True, slots=True)
class Result:
    symbol: str
    direction: str
    touch_at: str
    activation_at: str | None
    outcome: Outcome
    baseline_event_at: str | None
    first_structure_state: str | None
    first_structure_confirmed_at: str | None
    first_structure_early_60m: bool | None
    favorable_events: int
    adverse_events: int
    structural_balance: str


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def load_zone_events(path: Path, symbol: str) -> list[ZoneEvent]:
    result: list[ZoneEvent] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("symbol") or "").upper() != symbol:
                continue
            outcome = str(row.get("outcome") or "")
            outcome_at = str(row.get("outcome_at") or "").strip()
            if outcome not in {"bounce", "false_break_reclaim", "clean_break"}:
                continue
            if not outcome_at:
                continue
            result.append(
                ZoneEvent(
                    symbol=symbol,
                    role=str(row["role"]),
                    event_at=parse_dt(str(row["event_at"])),
                    outcome=outcome,
                    outcome_at=parse_dt(outcome_at),
                )
            )
    result.sort(key=lambda item: (item.outcome_at, item.event_at))
    return result


def classify(direction: str, event: ZoneEvent) -> tuple[State, str]:
    protective_role = "support" if direction == "Long" else "resistance"
    protective = event.role == protective_role
    clean = event.outcome == "clean_break"
    if protective and clean:
        return "protective_clean_break_against", "adverse"
    if protective:
        return "protective_hold_reclaim", "favorable"
    if clean:
        return "obstacle_clean_break_with", "favorable"
    return "obstacle_rejection_against", "adverse"


def path_limits(
    signal: Signal,
    archives: dict[str, Path],
    cache: TradeDayCache,
    *,
    horizon_hours: int,
    activation_pct: float,
    target_pct: float,
    stop_pct: float,
) -> tuple[datetime | None, Outcome, datetime | None]:
    start_ts = signal.touch_at.timestamp()
    end_ts = (signal.touch_at + timedelta(hours=horizon_hours)).timestamp()
    activation_ts: float | None = None
    for day in days_for(signal.touch_at, horizon_hours):
        archive = archives.get(day)
        if archive is None:
            continue
        tape = cache.get(archive)
        left = bisect.bisect_left(tape.timestamps, start_ts)
        right = bisect.bisect_right(tape.timestamps, end_ts)
        for index in range(left, right):
            timestamp = tape.timestamps[index]
            move = directional_move_pct(
                signal.direction, signal.entry_price, tape.prices[index]
            )
            if activation_ts is None and move >= activation_pct:
                activation_ts = timestamp
            if move <= -stop_pct:
                return dt(activation_ts), "stop_first", dt(timestamp)
            if move >= target_pct:
                return dt(activation_ts), "target_first", dt(timestamp)
    return dt(activation_ts), "unresolved_24h", None


def dt(timestamp: float | None) -> datetime | None:
    return None if timestamp is None else datetime.fromtimestamp(timestamp, UTC)


def analyse_signal(
    signal: Signal,
    activation_at: datetime | None,
    outcome: Outcome,
    baseline_at: datetime | None,
    events: list[ZoneEvent],
    *,
    horizon_hours: int,
) -> Result:
    limit = baseline_at or signal.touch_at + timedelta(hours=horizon_hours)
    causal = [] if activation_at is None else [
        event
        for event in events
        if event.event_at >= activation_at and event.outcome_at <= limit
    ]
    classified = [(event, *classify(signal.direction, event)) for event in causal]
    favorable = sum(sign == "favorable" for _, _, sign in classified)
    adverse = sum(sign == "adverse" for _, _, sign in classified)
    balance = "none"
    if favorable > adverse:
        balance = "net_favorable"
    elif adverse > favorable:
        balance = "net_adverse"
    elif favorable:
        balance = "balanced"
    first = classified[0] if classified else None
    early = None
    if first is not None and activation_at is not None:
        early = first[0].outcome_at <= activation_at + timedelta(minutes=60)
    return Result(
        symbol=signal.symbol,
        direction=signal.direction,
        touch_at=signal.touch_at.isoformat(),
        activation_at=None if activation_at is None else activation_at.isoformat(),
        outcome=outcome,
        baseline_event_at=None if baseline_at is None else baseline_at.isoformat(),
        first_structure_state=None if first is None else first[1],
        first_structure_confirmed_at=None if first is None else first[0].outcome_at.isoformat(),
        first_structure_early_60m=early,
        favorable_events=favorable,
        adverse_events=adverse,
        structural_balance=balance,
    )


def grouped(rows: list[Result], attr: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    values = sorted({str(getattr(row, attr)) for row in rows})
    for value in values:
        items = [row for row in rows if str(getattr(row, attr)) == value]
        resolved = [row for row in items if row.outcome != "unresolved_24h"]
        wins = sum(row.outcome == "target_first" for row in resolved)
        result.append(
            {
                "group": value,
                "signals": len(items),
                "resolved": len(resolved),
                "target_first": wins,
                "stop_first": len(resolved) - wins,
                "target_pct_resolved": round(100 * wins / len(resolved), 4)
                if resolved
                else None,
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Join frozen causal zone events to exact-touch paths")
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--zone-events", type=Path, required=True)
    parser.add_argument("--raw-symbol-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--fraction", type=float, default=1.0)
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--activation-pct", type=float, default=0.10)
    parser.add_argument("--target-pct", type=float, default=1.10)
    parser.add_argument("--stop-pct", type=float, default=1.00)
    args = parser.parse_args()
    if not 0 < args.fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    symbol = args.symbol.strip().upper()
    all_signals = load_signals(args.signals, symbol, 1.0)
    signals = all_signals[: max(1, math.ceil(len(all_signals) * args.fraction))]
    zones = load_zone_events(args.zone_events, symbol)
    archives = archive_map(args.raw_symbol_dir, symbol)
    cache = TradeDayCache(max_days=3)
    started = time.monotonic()
    rows: list[Result] = []
    for index, signal in enumerate(signals, 1):
        activation, outcome, baseline_at = path_limits(
            signal,
            archives,
            cache,
            horizon_hours=args.horizon_hours,
            activation_pct=args.activation_pct,
            target_pct=args.target_pct,
            stop_pct=args.stop_pct,
        )
        rows.append(
            analyse_signal(
                signal,
                activation,
                outcome,
                baseline_at,
                zones,
                horizon_hours=args.horizon_hours,
            )
        )
        if index % 25 == 0 or index == len(signals):
            print(f"{symbol}: {index}/{len(signals)}", flush=True)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with (output / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Result.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(row) for row in rows)
    summary = {
        "symbol": symbol,
        "total_available_signals": len(all_signals),
        "processed_signals": len(rows),
        "fraction": args.fraction,
        "zone_events_available": len(zones),
        "outcomes": dict(Counter(row.outcome for row in rows)),
        "first_structure": grouped(rows, "first_structure_state"),
        "structural_balance": grouped(rows, "structural_balance"),
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
