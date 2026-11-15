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

Outcome = Literal[
    "initial_stop",
    "protected_stop_plus_0p10",
    "target_plus_1p10",
    "unresolved_no_activation_24h",
    "alive_after_activation_24h",
]


@dataclass(frozen=True, slots=True)
class Result:
    symbol: str
    direction: str
    touch_at: str
    entry_price: float
    activated: bool
    activation_at: str | None
    seconds_to_activation: float | None
    outcome: Outcome
    event_at: str | None
    seconds_to_event: float | None


def scan(
    signal: Signal,
    archives: dict[str, Path],
    cache: TradeDayCache,
    *,
    horizon_hours: int,
    initial_stop_pct: float,
    activation_pct: float,
    protected_floor_pct: float,
    target_pct: float,
) -> Result:
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
            if activation_ts is None:
                if move <= -initial_stop_pct:
                    return result(signal, False, None, "initial_stop", timestamp)
                if move >= target_pct:
                    return result(signal, True, timestamp, "target_plus_1p10", timestamp)
                if move >= activation_pct:
                    activation_ts = timestamp
                continue
            if move <= protected_floor_pct:
                return result(
                    signal, True, activation_ts, "protected_stop_plus_0p10", timestamp
                )
            if move >= target_pct:
                return result(signal, True, activation_ts, "target_plus_1p10", timestamp)
    if activation_ts is None:
        return result(signal, False, None, "unresolved_no_activation_24h", None)
    return result(signal, True, activation_ts, "alive_after_activation_24h", None)


def result(
    signal: Signal,
    activated: bool,
    activation_ts: float | None,
    outcome: Outcome,
    event_ts: float | None,
) -> Result:
    start_ts = signal.touch_at.timestamp()
    iso = lambda value: None if value is None else datetime.fromtimestamp(value, UTC).isoformat()
    return Result(
        symbol=signal.symbol,
        direction=signal.direction,
        touch_at=signal.touch_at.isoformat(),
        entry_price=signal.entry_price,
        activated=activated,
        activation_at=iso(activation_ts),
        seconds_to_activation=None if activation_ts is None else activation_ts - start_ts,
        outcome=outcome,
        event_at=iso(event_ts),
        seconds_to_event=None if event_ts is None else event_ts - start_ts,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="+0.15 activation, +0.10 protected stop replay")
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--raw-symbol-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--fraction", type=float, default=1.0)
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--initial-stop-pct", type=float, default=1.0)
    parser.add_argument("--activation-pct", type=float, default=0.15)
    parser.add_argument("--protected-floor-pct", type=float, default=0.10)
    parser.add_argument("--target-pct", type=float, default=1.10)
    args = parser.parse_args()
    if not 0 < args.fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    if not 0 <= args.protected_floor_pct < args.activation_pct < args.target_pct:
        raise ValueError("require protected floor < activation < target")
    symbol = args.symbol.strip().upper()
    all_signals = load_signals(args.signals, symbol, 1.0)
    count = max(1, math.ceil(len(all_signals) * args.fraction))
    signals = all_signals[:count]
    archives = archive_map(args.raw_symbol_dir, symbol)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = TradeDayCache(max_days=3)
    started = time.monotonic()
    rows: list[Result] = []
    for index, signal in enumerate(signals, 1):
        rows.append(
            scan(
                signal,
                archives,
                cache,
                horizon_hours=args.horizon_hours,
                initial_stop_pct=args.initial_stop_pct,
                activation_pct=args.activation_pct,
                protected_floor_pct=args.protected_floor_pct,
                target_pct=args.target_pct,
            )
        )
        if index % 25 == 0 or index == len(signals):
            print(f"{symbol}: {index}/{len(signals)}", flush=True)
    counts = dict(Counter(item.outcome for item in rows))
    activated = sum(item.activated for item in rows)
    target = counts.get("target_plus_1p10", 0)
    protected = counts.get("protected_stop_plus_0p10", 0)
    alive = counts.get("alive_after_activation_24h", 0)
    summary = {
        "symbol": symbol,
        "total_available_signals": len(all_signals),
        "processed_signals": len(rows),
        "fraction": args.fraction,
        "horizon_hours": args.horizon_hours,
        "initial_stop_pct": args.initial_stop_pct,
        "activation_pct": args.activation_pct,
        "protected_floor_pct": args.protected_floor_pct,
        "target_pct": args.target_pct,
        "activated": activated,
        "outcomes": counts,
        "target_pct_of_activated": round(100 * target / activated, 4) if activated else None,
        "survived_protected_floor_count": target + alive,
        "survived_protected_floor_pct_of_activated": round(
            100 * (target + alive) / activated, 4
        ) if activated else None,
        "protected_stop_pct_of_activated": round(100 * protected / activated, 4)
        if activated
        else None,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    with (output / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Result.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(asdict(item) for item in rows)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
