from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from bybit_workbench.research.exit_break_even_v13 import (
    TradeDayCache,
    directional_move_pct,
)
from bybit_workbench.research.mtf_entry import Direction

Outcome = Literal["target_first", "stop_first", "unresolved_24h"]


@dataclass(frozen=True, slots=True)
class Signal:
    symbol: str
    direction: Direction
    touch_at: datetime
    entry_price: float


@dataclass(frozen=True, slots=True)
class Result:
    symbol: str
    direction: Direction
    touch_at: str
    entry_price: float
    outcome: Outcome
    event_at: str | None
    seconds_to_event: float | None
    max_favorable_before_event_pct: float
    max_favorable_at: str | None
    seconds_to_max_favorable: float | None
    ever_positive_before_stop: bool | None
    complete_horizon: bool


def archive_map(raw_symbol_dir: Path, symbol: str) -> dict[str, Path]:
    root = raw_symbol_dir / "public_trades"
    result: dict[str, Path] = {}
    for path in root.glob(f"{symbol}*.csv.gz"):
        day = path.name.removeprefix(symbol).removesuffix(".csv.gz")
        result[day] = path
    return result


def load_signals(path: Path, symbol: str, fraction: float) -> list[Signal]:
    items: list[Signal] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            row_symbol = str(row.get("symbol") or "").upper()
            if row_symbol != symbol:
                continue
            touch_at = str(row.get("touch_at") or "").strip()
            # Some historical P31 exports retain untouched candidates as rows
            # with an empty touch_at.  The panel's exact_touch count excludes
            # them, so they must not enter this replay.
            if not touch_at:
                continue
            direction = str(row.get("direction") or "")
            if direction not in {"Long", "Short"}:
                raise ValueError(f"invalid direction: {direction}")
            items.append(
                Signal(
                    symbol=row_symbol,
                    direction=direction,  # type: ignore[arg-type]
                    touch_at=datetime.fromisoformat(touch_at).astimezone(UTC),
                    entry_price=float(row["entry_price"]),
                )
            )
    items.sort(key=lambda item: item.touch_at)
    if not items:
        raise ValueError(f"no {symbol} signals in {path}")
    count = max(1, math.ceil(len(items) * fraction))
    return items[:count]


def days_for(start: datetime, horizon_hours: int) -> list[str]:
    end = start + timedelta(hours=horizon_hours)
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    result: list[str] = []
    while current <= last:
        result.append(current.isoformat())
        current += timedelta(days=1)
    return result


def scan_signal(
    signal: Signal,
    archives: dict[str, Path],
    cache: TradeDayCache,
    *,
    horizon_hours: int,
    target_pct: float,
    stop_pct: float,
) -> Result:
    start_ts = signal.touch_at.timestamp()
    end_ts = (signal.touch_at + timedelta(hours=horizon_hours)).timestamp()
    max_favorable = 0.0
    max_favorable_ts: float | None = None
    observed = False
    available_last_day = max(archives, default="")

    for day in days_for(signal.touch_at, horizon_hours):
        archive = archives.get(day)
        if archive is None:
            if day <= available_last_day:
                raise FileNotFoundError(f"missing internal trade day {signal.symbol} {day}")
            break
        tape = cache.get(archive)
        left = bisect.bisect_left(tape.timestamps, start_ts)
        right = bisect.bisect_right(tape.timestamps, end_ts)
        for index in range(left, right):
            observed = True
            timestamp = tape.timestamps[index]
            move = directional_move_pct(
                signal.direction, signal.entry_price, tape.prices[index]
            )
            if move > max_favorable:
                max_favorable = move
                max_favorable_ts = timestamp
            # Conservative ordering: a recorded trade is evaluated once and stop
            # wins an exact boundary ambiguity.
            if move <= -stop_pct:
                return make_result(
                    signal,
                    "stop_first",
                    timestamp,
                    max_favorable,
                    max_favorable_ts,
                    True,
                )
            if move >= target_pct:
                return make_result(
                    signal,
                    "target_first",
                    timestamp,
                    max_favorable,
                    max_favorable_ts,
                    True,
                )

    return make_result(
        signal,
        "unresolved_24h",
        None,
        max_favorable,
        max_favorable_ts,
        observed and end_ts <= datetime.now(UTC).timestamp(),
    )


def make_result(
    signal: Signal,
    outcome: Outcome,
    event_ts: float | None,
    max_favorable: float,
    max_favorable_ts: float | None,
    complete_horizon: bool,
) -> Result:
    stopped = outcome == "stop_first"
    return Result(
        symbol=signal.symbol,
        direction=signal.direction,
        touch_at=signal.touch_at.isoformat(),
        entry_price=signal.entry_price,
        outcome=outcome,
        event_at=None if event_ts is None else datetime.fromtimestamp(event_ts, UTC).isoformat(),
        seconds_to_event=None if event_ts is None else max(0.0, event_ts - signal.touch_at.timestamp()),
        max_favorable_before_event_pct=round(max(0.0, max_favorable), 8),
        max_favorable_at=(
            None
            if max_favorable_ts is None
            else datetime.fromtimestamp(max_favorable_ts, UTC).isoformat()
        ),
        seconds_to_max_favorable=(
            None
            if max_favorable_ts is None
            else max(0.0, max_favorable_ts - signal.touch_at.timestamp())
        ),
        ever_positive_before_stop=(max_favorable > 0.0) if stopped else None,
        complete_horizon=complete_horizon,
    )


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize(results: list[Result], *, total_available: int, fraction: float) -> dict[str, object]:
    stopped = [item for item in results if item.outcome == "stop_first"]
    mfe = [item.max_favorable_before_event_pct for item in stopped]
    positive = [value for value in mfe if value > 0]
    bands = [(0.0, 0.0), (0.0, 0.1), (0.1, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.1)]
    band_counts: dict[str, int] = {"exactly_0": sum(value == 0 for value in mfe)}
    for low, high in bands[1:]:
        band_counts[f"gt_{low:g}_lt_{high:g}"] = sum(low < value < high for value in mfe)
    return {
        "total_available_signals": total_available,
        "fraction": fraction,
        "processed_signals": len(results),
        "outcomes": dict(Counter(item.outcome for item in results)),
        "stop_first": {
            "count": len(stopped),
            "never_positive_count": sum(value == 0 for value in mfe),
            "ever_positive_count": len(positive),
            "ever_positive_pct": round(100 * len(positive) / len(stopped), 4) if stopped else None,
            "mfe_pct": {
                "mean": round(statistics.fmean(mfe), 8) if mfe else None,
                "median": percentile(mfe, 0.5),
                "p25": percentile(mfe, 0.25),
                "p75": percentile(mfe, 0.75),
                "p90": percentile(mfe, 0.9),
                "p95": percentile(mfe, 0.95),
                "max": max(mfe, default=None),
            },
            "depth_bands": band_counts,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exact-touch +1.10/-1.00 replay and favorable depth before stop."
    )
    parser.add_argument("--signals", type=Path, required=True)
    parser.add_argument("--raw-symbol-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--fraction", type=float, default=1.0)
    parser.add_argument("--horizon-hours", type=int, default=24)
    parser.add_argument("--target-pct", type=float, default=1.10)
    parser.add_argument("--stop-pct", type=float, default=1.00)
    args = parser.parse_args()
    if not 0 < args.fraction <= 1:
        raise ValueError("fraction must be in (0, 1]")
    symbol = args.symbol.strip().upper()
    all_signals = load_signals(args.signals, symbol, 1.0)
    signals = load_signals(args.signals, symbol, args.fraction)
    archives = archive_map(args.raw_symbol_dir, symbol)
    if not archives:
        raise FileNotFoundError(f"no public trade archives under {args.raw_symbol_dir}")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache = TradeDayCache(max_days=3)
    started = time.monotonic()
    results: list[Result] = []
    for index, signal in enumerate(signals, start=1):
        results.append(
            scan_signal(
                signal,
                archives,
                cache,
                horizon_hours=args.horizon_hours,
                target_pct=args.target_pct,
                stop_pct=args.stop_pct,
            )
        )
        if index == len(signals) or index % 25 == 0:
            print(f"{symbol}: {index}/{len(signals)}", flush=True)

    fields = [field.name for field in Result.__dataclass_fields__.values()]
    with (output / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(asdict(item) for item in results)
    summary = summarize(results, total_available=len(all_signals), fraction=args.fraction)
    summary.update(
        {
            "symbol": symbol,
            "horizon_hours": args.horizon_hours,
            "target_pct": args.target_pct,
            "stop_pct": args.stop_pct,
            "wall_seconds": round(time.monotonic() - started, 3),
        }
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
