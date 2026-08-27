from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    SignalSource,
    TradeDayCache,
    directional_move_pct,
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
BASELINE_SIMPLE = "A_SIMPLE_TAKE_1P00"
EXPECTED_SIMPLE_REASONS = {
    "initial_stop": 66,
    "early_be": 851,
    "core_take": 142,
    "data_end": 4,
}
EXPECTED_OLD_GROSS = 76.065635

Outcome = Literal[
    "floor_minus_0p10",
    "reached_1p10",
    "data_end_after_activation",
    "data_end_no_activation",
]


@dataclass(frozen=True, slots=True)
class Config:
    initial_stop_pct: float = 1.0
    activation_pct: float = 0.10
    floor_pct: float = -0.10
    continuation_pct: float = 1.10
    horizon_hours: int = 72
    day_cache_size: int = 6
    progress_interval_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not self.floor_pct < self.activation_pct < self.continuation_pct:
            raise ValueError("require floor < activation < continuation")
        if self.initial_stop_pct <= 0 or self.horizon_hours <= 0:
            raise ValueError("invalid stop/horizon")


@dataclass(frozen=True, slots=True)
class BaselineRow:
    symbol: str
    touch_at: datetime
    exit_reason: str
    exit_move_pct: float


@dataclass(frozen=True, slots=True)
class Result:
    symbol: str
    touch_at: datetime
    outcome: Outcome
    activation_at: datetime | None
    event_at: datetime | None
    seconds_activation_to_event: float | None
    same_timestamp_event: bool


class Progress:
    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.started = time.monotonic()
        self.last = 0.0

    def emit(self, processed: int, total: int, *, force: bool = False, detail: str = "") -> None:
        now = time.monotonic()
        if not force and now - self.last < self.interval:
            return
        elapsed = now - self.started
        eta = (
            None
            if processed <= 0 or processed >= total
            else elapsed / processed * (total - processed)
        )
        suffix = f" | {detail}" if detail else ""
        print(
            f"[P47J] processed={processed}/{total} elapsed={_duration(elapsed)} "
            f"ETA={'n/a' if eta is None else _duration(eta)}{suffix}",
            flush=True,
        )
        self.last = now


def _duration(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    hours, rem = divmod(value, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


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


def discover_latest_p47h(root: Path) -> Path:
    report_root = root / "reports" / "trailing_ladder_v1"
    candidates = sorted(path for path in report_root.glob("ALL9_*") if path.is_dir())
    if not candidates:
        raise FileNotFoundError(f"no P47H ALL9 report under {report_root}")
    return candidates[-1]


def load_baseline(path: Path) -> dict[tuple[str, datetime], BaselineRow]:
    result: dict[tuple[str, datetime], BaselineRow] = {}
    counts: dict[str, int] = {}
    gross = 0.0
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("policy_id") or "") != BASELINE_SIMPLE:
                continue
            item = BaselineRow(
                symbol=str(row.get("symbol") or ""),
                touch_at=datetime.fromisoformat(str(row.get("touch_at") or "")).astimezone(UTC),
                exit_reason=str(row.get("exit_reason") or ""),
                exit_move_pct=float(row.get("exit_move_pct") or 0.0),
            )
            key = (item.symbol, item.touch_at)
            if key in result:
                raise ValueError(f"duplicate baseline key: {key}")
            result[key] = item
            counts[item.exit_reason] = counts.get(item.exit_reason, 0) + 1
            gross += item.exit_move_pct
    if len(result) != EXPECTED_ALL9:
        raise ValueError(f"baseline row count {len(result)} != {EXPECTED_ALL9}")
    if counts != EXPECTED_SIMPLE_REASONS:
        raise ValueError(f"baseline reason guardrail failed: {counts}")
    if abs(gross - EXPECTED_OLD_GROSS) > 1e-6:
        raise ValueError(f"baseline gross guardrail failed: {gross:.6f}")
    return result


def signal_map(sources: tuple[SignalSource, ...]) -> dict[tuple[str, datetime], CoreSignal]:
    result: dict[tuple[str, datetime], CoreSignal] = {}
    counts: dict[str, int] = {}
    for source in sources:
        signals = load_core_signals(source)
        counts[source.symbol] = len(signals)
        for signal in signals:
            result[(signal.symbol, signal.touch_at)] = signal
    if counts != EXPECTED_COUNTS or len(result) != EXPECTED_ALL9:
        raise ValueError(f"frozen P40 guardrail failed: counts={counts} total={len(result)}")
    return result


def _days(start: datetime, hours: int) -> tuple[str, ...]:
    end = start + timedelta(hours=hours)
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    values: list[str] = []
    while current <= last:
        values.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(values)


def scan(
    signal: CoreSignal,
    archives: dict[str, Path],
    cache: TradeDayCache,
    config: Config,
) -> Result:
    start_ts = signal.touch_at.timestamp()
    end_ts = (signal.touch_at + timedelta(hours=config.horizon_hours)).timestamp()
    activation_ts: float | None = None
    available_days = sorted(archives)
    max_day = available_days[-1] if available_days else ""

    for day in _days(signal.touch_at, config.horizon_hours):
        archive = archives.get(day)
        if archive is None:
            if day <= max_day:
                raise FileNotFoundError(f"internal trade-day gap {signal.symbol} {day}")
            break
        tape = cache.get(archive)
        left = bisect.bisect_left(tape.timestamps, start_ts)
        right = bisect.bisect_right(tape.timestamps, end_ts)
        for index in range(left, right):
            timestamp = tape.timestamps[index]
            move = directional_move_pct(
                signal.direction,
                signal.entry_price,
                tape.prices[index],
            )
            if activation_ts is None:
                if move <= -config.initial_stop_pct:
                    raise ValueError(
                        "non-initial-stop baseline signal hit -1 before +0.10: "
                        f"{signal.symbol} {signal.touch_at.isoformat()}"
                    )
                if move >= config.activation_pct:
                    activation_ts = timestamp
                continue

            if move <= config.floor_pct:
                activation_at = datetime.fromtimestamp(activation_ts, UTC)
                event_at = datetime.fromtimestamp(timestamp, UTC)
                return Result(
                    signal.symbol,
                    signal.touch_at,
                    "floor_minus_0p10",
                    activation_at,
                    event_at,
                    max(0.0, timestamp - activation_ts),
                    timestamp == activation_ts,
                )
            if move >= config.continuation_pct:
                activation_at = datetime.fromtimestamp(activation_ts, UTC)
                event_at = datetime.fromtimestamp(timestamp, UTC)
                return Result(
                    signal.symbol,
                    signal.touch_at,
                    "reached_1p10",
                    activation_at,
                    event_at,
                    max(0.0, timestamp - activation_ts),
                    timestamp == activation_ts,
                )

    activation_at_final: datetime | None = (
        None if activation_ts is None else datetime.fromtimestamp(activation_ts, UTC)
    )
    outcome: Outcome = (
        "data_end_no_activation" if activation_ts is None else "data_end_after_activation"
    )
    return Result(
        signal.symbol, signal.touch_at, outcome, activation_at_final, None, None, False
    )


def _scope(symbol: str) -> str:
    return "DEV2" if symbol in DEV_SYMBOLS else "HOLDOUT7"


def summarize(results: list[Result], scope: str) -> dict[str, Any]:
    if scope == "ALL9":
        items = results
    elif scope == "DEV2":
        items = [item for item in results if item.symbol in DEV_SYMBOLS]
    elif scope == "HOLDOUT7":
        items = [item for item in results if item.symbol in HOLDOUT_SYMBOLS]
    else:
        items = [item for item in results if item.symbol == scope]
    activated = [item for item in items if item.activation_at is not None]
    floor = [item for item in activated if item.outcome == "floor_minus_0p10"]
    reached = [item for item in activated if item.outcome == "reached_1p10"]
    alive = [item for item in activated if item.outcome == "data_end_after_activation"]
    delays: list[float] = []
    for item in floor:
        if item.seconds_activation_to_event is not None:
            delays.append(item.seconds_activation_to_event)
    retained = len(reached) + len(alive)
    return {
        "scope": scope,
        "scanned_non_initial": len(items),
        "activated_plus_0p10": len(activated),
        "stopped_minus_0p10": len(floor),
        "reached_plus_1p10_first": len(reached),
        "still_alive_at_data_end": len(alive),
        "remain_in_battle": retained,
        "remain_pct_of_activated": (
            round(100.0 * retained / len(activated), 6) if activated else None
        ),
        "reach_1p10_pct_of_activated": (
            round(100.0 * len(reached) / len(activated), 6) if activated else None
        ),
        "floor_stop_pct_of_activated": (
            round(100.0 * len(floor) / len(activated), 6) if activated else None
        ),
        "median_seconds_to_minus_0p10": (
            round(float(statistics.median(delays)), 6) if delays else None
        ),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(
    root: Path,
    *,
    baseline_dir: Path | None = None,
    output_dir: Path | None = None,
    config: Config | None = None,
) -> Path:
    cfg = config or Config()
    baseline_root = baseline_dir or discover_latest_p47h(root)
    baseline = load_baseline(baseline_root / "policy_results.csv")
    sources = discover_sources(root)
    signals = signal_map(sources)
    source_by_symbol = {source.symbol: source for source in sources}

    keys = sorted(
        (key for key, row in baseline.items() if row.exit_reason != "initial_stop"),
        key=lambda item: (item[0], item[1]),
    )
    if len(keys) != EXPECTED_ALL9 - EXPECTED_SIMPLE_REASONS["initial_stop"]:
        raise ValueError(f"expected 997 non-initial candidates, got {len(keys)}")

    reporter = Progress(cfg.progress_interval_seconds)
    reporter.emit(0, len(keys), force=True, detail="scan only until -0.10 or +1.10")
    caches = {symbol: TradeDayCache(max_days=cfg.day_cache_size) for symbol in ALL_SYMBOLS}
    archives = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    results: list[Result] = []
    for index, key in enumerate(keys, start=1):
        signal = signals[key]
        item = scan(signal, archives[signal.symbol], caches[signal.symbol], cfg)
        results.append(item)
        reporter.emit(
            index,
            len(keys),
            detail=(
                f"symbol={signal.symbol} outcome={item.outcome} "
                f"cache={caches[signal.symbol].hits}/{caches[signal.symbol].misses}"
            ),
        )
    reporter.emit(len(keys), len(keys), force=True, detail="done")

    all9_activated = sum(item.activation_at is not None for item in results)
    no_activation = sum(item.outcome == "data_end_no_activation" for item in results)
    if all9_activated != 995 or no_activation != 2:
        raise ValueError(
            "first-touch guardrail failed: "
            f"activated={all9_activated} no_activation={no_activation}"
        )

    scopes = ["ALL9", "DEV2", "HOLDOUT7", *ALL_SYMBOLS]
    summaries = [summarize(results, scope) for scope in scopes]

    if output_dir is None:
        stamp = datetime.now(UTC).strftime("ALL9_%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "early_protection_minus01_v1" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for item in results:
        row = asdict(item)
        for field in ("touch_at", "activation_at", "event_at"):
            value = row[field]
            row[field] = value.isoformat() if isinstance(value, datetime) else ""
        rows.append(row)
    rows.sort(key=lambda row: (str(row["symbol"]), str(row["touch_at"])))
    write_csv(output_dir / "event_results.csv", rows)
    write_csv(output_dir / "scope_summary.csv", summaries)

    payload = {
        "research": "P47J -0.10 protection quick survival",
        "created_at": datetime.now(UTC).isoformat(),
        "baseline_dir": str(baseline_root),
        "downloads": "DISABLED / fail-closed",
        "config": asdict(cfg),
        "guardrails": {
            "all9_signals": EXPECTED_ALL9,
            "old_initial_stops": 66,
            "expected_plus_0p10_first": 995,
            "expected_data_end_no_activation": 2,
        },
        "semantics": (
            "After first +0.10 touch, count first subsequent -0.10 floor versus "
            "+1.10 continuation. "
            "This is path survival, not PnL and not a new Exit optimization."
        ),
        "scope_summary": summaries,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    all9 = summaries[0]
    md = [
        "# P47J -0.10 protection quick survival",
        "",
        "Frozen Entry V1 unchanged. Downloads disabled.",
        "",
        "After first +0.10 favorable touch, the quick scan stops at the first of:",
        "- -0.10 protective floor",
        "- +1.10 continuation",
        "- frozen data end.",
        "",
        f"Activated +0.10: **{all9['activated_plus_0p10']}**",
        f"Stopped at -0.10 first: **{all9['stopped_minus_0p10']}**",
        f"Reached +1.10 first: **{all9['reached_plus_1p10_first']}**",
        f"Still alive at data end: **{all9['still_alive_at_data_end']}**",
        f"Remain in battle: **{all9['remain_in_battle']} ({all9['remain_pct_of_activated']}%)**",
        "",
        "This does not calculate fees, PnL, or downstream runner exits.",
    ]
    (output_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(
        "P47J ALL9: "
        f"activated={all9['activated_plus_0p10']} "
        f"stopped_minus_0p10={all9['stopped_minus_0p10']} "
        f"reached_1p10={all9['reached_plus_1p10_first']} "
        f"alive={all9['still_alive_at_data_end']} "
        f"remain={all9['remain_in_battle']} "
        f"remain_pct={all9['remain_pct_of_activated']}%",
        flush=True,
    )
    print(f"Report: {output_dir}", flush=True)
    return output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P47J quick survival with -0.10 floor after +0.10."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config(progress_interval_seconds=args.progress_interval_seconds)
    run(
        args.project_root.resolve(),
        baseline_dir=None if args.baseline_dir is None else args.baseline_dir.resolve(),
        output_dir=None if args.output_dir is None else args.output_dir.resolve(),
        config=cfg,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
