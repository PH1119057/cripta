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
OLD_EARLY_BE_COUNT = 851

Outcome = Literal[
    "baseline_initial_stop",
    "initial_stop_before_0p50",
    "floor_minus_0p50",
    "reached_1p10",
    "data_end_after_activation",
    "data_end_no_activation",
]


@dataclass(frozen=True, slots=True)
class Config:
    initial_stop_pct: float = 1.0
    activation_pct: float = 0.50
    floor_pct: float = -0.50
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
    old_exit_reason: str
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
            f"[P47K] processed={processed}/{total} elapsed={_duration(elapsed)} "
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
            key = (signal.symbol, signal.touch_at)
            if key in result:
                raise ValueError(f"duplicate frozen signal key: {key}")
            result[key] = signal
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
    old_exit_reason: str,
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
                    event_at = datetime.fromtimestamp(timestamp, UTC)
                    return Result(
                        signal.symbol,
                        signal.touch_at,
                        old_exit_reason,
                        "initial_stop_before_0p50",
                        None,
                        event_at,
                        None,
                        False,
                    )
                if move >= config.activation_pct:
                    activation_ts = timestamp
                    if move >= config.continuation_pct:
                        event_at = datetime.fromtimestamp(timestamp, UTC)
                        return Result(
                            signal.symbol,
                            signal.touch_at,
                            old_exit_reason,
                            "reached_1p10",
                            event_at,
                            event_at,
                            0.0,
                            True,
                        )
                continue

            if move <= config.floor_pct:
                activation_at = datetime.fromtimestamp(activation_ts, UTC)
                event_at = datetime.fromtimestamp(timestamp, UTC)
                return Result(
                    signal.symbol,
                    signal.touch_at,
                    old_exit_reason,
                    "floor_minus_0p50",
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
                    old_exit_reason,
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
        signal.symbol,
        signal.touch_at,
        old_exit_reason,
        outcome,
        activation_at_final,
        None,
        None,
        False,
    )


def _scope_items(results: list[Result], scope: str) -> list[Result]:
    if scope == "ALL9":
        return results
    if scope == "DEV2":
        return [item for item in results if item.symbol in DEV_SYMBOLS]
    if scope == "HOLDOUT7":
        return [item for item in results if item.symbol in HOLDOUT_SYMBOLS]
    return [item for item in results if item.symbol == scope]


def summarize(results: list[Result], scope: str, cohort: str = "ALL") -> dict[str, Any]:
    items = _scope_items(results, scope)
    if cohort == "OLD_EARLY_BE":
        items = [item for item in items if item.old_exit_reason == "early_be"]
    elif cohort != "ALL":
        raise ValueError(f"unsupported cohort: {cohort}")

    baseline_initial = [item for item in items if item.outcome == "baseline_initial_stop"]
    pre_activation_stop = [
        item for item in items if item.outcome == "initial_stop_before_0p50"
    ]
    activated = [item for item in items if item.activation_at is not None]
    floor = [item for item in activated if item.outcome == "floor_minus_0p50"]
    reached = [item for item in activated if item.outcome == "reached_1p10"]
    alive = [item for item in activated if item.outcome == "data_end_after_activation"]
    no_activation = [item for item in items if item.outcome == "data_end_no_activation"]
    delays = [
        float(item.seconds_activation_to_event)
        for item in floor
        if item.seconds_activation_to_event is not None
    ]
    remain = len(reached) + len(alive)

    return {
        "scope": scope,
        "cohort": cohort,
        "signals": len(items),
        "baseline_initial_stop": len(baseline_initial),
        "initial_stop_before_plus_0p50": len(pre_activation_stop),
        "activated_plus_0p50": len(activated),
        "stopped_minus_0p50": len(floor),
        "reached_plus_1p10_first": len(reached),
        "still_alive_at_data_end": len(alive),
        "data_end_no_activation": len(no_activation),
        "remain_in_battle": remain,
        "activation_pct_of_cohort": (
            round(100.0 * len(activated) / len(items), 6) if items else None
        ),
        "remain_pct_of_activated": (
            round(100.0 * remain / len(activated), 6) if activated else None
        ),
        "floor_stop_pct_of_activated": (
            round(100.0 * len(floor) / len(activated), 6) if activated else None
        ),
        "median_seconds_plus_0p50_to_minus_0p50": (
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

    scan_keys = sorted(
        (key for key, row in baseline.items() if row.exit_reason != "initial_stop"),
        key=lambda item: (item[0], item[1]),
    )
    expected_scan = EXPECTED_ALL9 - EXPECTED_SIMPLE_REASONS["initial_stop"]
    if len(scan_keys) != expected_scan:
        raise ValueError(f"expected {expected_scan} scan candidates, got {len(scan_keys)}")

    results: list[Result] = []
    for row in baseline.values():
        if row.exit_reason != "initial_stop":
            continue
        results.append(
            Result(
                row.symbol,
                row.touch_at,
                row.exit_reason,
                "baseline_initial_stop",
                None,
                None,
                None,
                False,
            )
        )

    reporter = Progress(cfg.progress_interval_seconds)
    reporter.emit(
        0,
        len(scan_keys),
        force=True,
        detail="keep -1 until +0.50; then first -0.50 vs +1.10",
    )
    caches = {symbol: TradeDayCache(max_days=cfg.day_cache_size) for symbol in ALL_SYMBOLS}
    archives = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    for index, key in enumerate(scan_keys, start=1):
        signal = signals[key]
        old_reason = baseline[key].exit_reason
        item = scan(
            signal,
            old_reason,
            archives[signal.symbol],
            caches[signal.symbol],
            cfg,
        )
        results.append(item)
        reporter.emit(
            index,
            len(scan_keys),
            detail=(
                f"symbol={signal.symbol} old={old_reason} outcome={item.outcome} "
                f"cache={caches[signal.symbol].hits}/{caches[signal.symbol].misses}"
            ),
        )
    reporter.emit(len(scan_keys), len(scan_keys), force=True, detail="done")

    if len(results) != EXPECTED_ALL9:
        raise ValueError(f"result count {len(results)} != {EXPECTED_ALL9}")
    old_be_results = [item for item in results if item.old_exit_reason == "early_be"]
    if len(old_be_results) != OLD_EARLY_BE_COUNT:
        raise ValueError(
            f"old early_be cohort {len(old_be_results)} != {OLD_EARLY_BE_COUNT}"
        )

    scopes = ["ALL9", "DEV2", "HOLDOUT7", *ALL_SYMBOLS]
    summaries: list[dict[str, Any]] = []
    for scope in scopes:
        summaries.append(summarize(results, scope, "ALL"))
        summaries.append(summarize(results, scope, "OLD_EARLY_BE"))

    if output_dir is None:
        stamp = datetime.now(UTC).strftime("ALL9_%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "early_protection_plus05_minus05_v1" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    for item in results:
        output_row: dict[str, Any] = asdict(item)
        for field_name in ("touch_at", "activation_at", "event_at"):
            value = output_row[field_name]
            output_row[field_name] = (
                value.isoformat() if isinstance(value, datetime) else ""
            )
        rows.append(output_row)
    rows.sort(
        key=lambda output_row: (
            str(output_row["symbol"]),
            str(output_row["touch_at"]),
        )
    )
    write_csv(output_dir / "event_results.csv", rows)
    write_csv(output_dir / "scope_summary.csv", summaries)

    source_rows = [
        {
            "symbol": source.symbol,
            "p40_dir": str(source.p40_dir),
            "features_path": str(source.features_path),
            "dataset_dir": str(source.dataset_dir),
        }
        for source in sources
    ]
    write_csv(output_dir / "sources.csv", source_rows)

    all9 = summarize(results, "ALL9", "ALL")
    old_be = summarize(results, "ALL9", "OLD_EARLY_BE")
    payload = {
        "research": "P47K +0.50 activation -> -0.50 floor quick survival",
        "created_at": datetime.now(UTC).isoformat(),
        "baseline_dir": str(baseline_root),
        "downloads": "DISABLED / fail-closed",
        "config": asdict(cfg),
        "guardrails": {
            "all9_signals": EXPECTED_ALL9,
            "baseline_initial_stops": EXPECTED_SIMPLE_REASONS["initial_stop"],
            "old_early_be_cohort": OLD_EARLY_BE_COUNT,
            "entry_v1": "frozen / unchanged",
        },
        "semantics": (
            "Keep the original -1.00% stop until first +0.50% favorable touch. "
            "Then move the protective floor to -0.50% and count the first subsequent "
            "-0.50% floor versus +1.10% continuation. This is path survival only, "
            "not PnL optimization. The OLD_EARLY_BE cohort isolates the 851 trades "
            "that the legacy research closed at theoretical 0.00%."
        ),
        "scope_summary": summaries,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    md = [
        "# P47K +0.50 -> -0.50 quick survival",
        "",
        "Frozen Entry V1 unchanged. Downloads disabled.",
        "",
        "Rule under test:",
        "- keep initial stop at -1.00% until first +0.50% favorable touch",
        "- after +0.50%, move floor to -0.50%",
        "- then stop scan at first -0.50% or +1.10% continuation",
        "",
        "## ALL9",
        f"Signals: **{all9['signals']}**",
        f"Baseline -1 stops: **{all9['baseline_initial_stop']}**",
        (
            "Additional -1 stops before +0.50: "
            f"**{all9['initial_stop_before_plus_0p50']}**"
        ),
        f"Activated +0.50: **{all9['activated_plus_0p50']}**",
        f"Stopped at -0.50 after activation: **{all9['stopped_minus_0p50']}**",
        f"Reached +1.10 first: **{all9['reached_plus_1p10_first']}**",
        f"Remain in battle: **{all9['remain_in_battle']}**",
        "",
        "## Legacy 851 theoretical-BE cohort",
        f"Old early_be trades: **{old_be['signals']}**",
        (
            "Reached +0.50 before the original -1 stop: "
            f"**{old_be['activated_plus_0p50']}**"
        ),
        (
            "Then stopped at -0.50 before +1.10: "
            f"**{old_be['stopped_minus_0p50']}**"
        ),
        (
            "Then reached +1.10 first: "
            f"**{old_be['reached_plus_1p10_first']}**"
        ),
        f"Remain in battle: **{old_be['remain_in_battle']}**",
        "",
        "No fees, PnL, or downstream runner exits are calculated here.",
    ]
    (output_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(
        "P47K ALL9: "
        f"baseline_stop={all9['baseline_initial_stop']} "
        f"pre05_stop={all9['initial_stop_before_plus_0p50']} "
        f"activated05={all9['activated_plus_0p50']} "
        f"stopped_m05={all9['stopped_minus_0p50']} "
        f"reached_1p10={all9['reached_plus_1p10_first']} "
        f"remain={all9['remain_in_battle']}",
        flush=True,
    )
    print(
        "P47K OLD_EARLY_BE_851: "
        f"activated05={old_be['activated_plus_0p50']} "
        f"stopped_m05={old_be['stopped_minus_0p50']} "
        f"reached_1p10={old_be['reached_plus_1p10_first']} "
        f"remain={old_be['remain_in_battle']}",
        flush=True,
    )
    print(f"Report: {output_dir}", flush=True)
    return output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P47K keep -1 until +0.50, then -0.50 floor quick survival."
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
