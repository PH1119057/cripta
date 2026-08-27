from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
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
EXPECTED_EARLY_FAILURES = 66
EXPECTED_DEV_FAILURES = 16
EXPECTED_HOLDOUT_FAILURES = 50

FailureClass = Literal[
    "puncture_recovered_before_3",
    "deep_3_then_recovered",
    "deep_3_no_recovery",
    "no_recovery_no_3",
]


@dataclass(frozen=True, slots=True)
class PunctureConfig:
    initial_stop_pct: float = 1.0
    deep_break_pct: float = 3.0
    near_zone_level_pct: float = -0.10
    entry_recovery_level_pct: float = 0.0
    horizon_hours: int = 72
    day_cache_size: int = 6
    progress_interval_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if self.deep_break_pct <= self.initial_stop_pct:
            raise ValueError("deep_break_pct must be greater than initial_stop_pct")
        if self.near_zone_level_pct >= self.entry_recovery_level_pct:
            raise ValueError("near_zone_level_pct must be below entry recovery level")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class EarlyFailureEvent:
    symbol: str
    direction: str
    touch_at: str
    entry_price: float
    source_scope: str
    first_minus_1_at: str
    first_minus_1_seconds: float
    first_near_zone_at: str | None
    seconds_to_near_zone: float | None
    first_entry_recovery_at: str | None
    seconds_to_entry_recovery: float | None
    first_minus_3_at: str | None
    seconds_to_minus_3: float | None
    seconds_minus_1_to_minus_3: float | None
    continuous_below_minus_1_seconds: float | None
    deepest_move_72h_pct: float
    deepest_before_entry_recovery_pct: float
    recovered_near_zone_72h: bool
    recovered_entry_72h: bool
    hit_minus_3_72h: bool
    hit_minus_3_before_entry_recovery: bool
    class_name: FailureClass
    completed_72h: bool
    missing_archive_days: str


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
            f"[P47G] processed={processed}/{total} "
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


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _key(symbol: str, touch_at: datetime) -> tuple[str, str]:
    return symbol, touch_at.astimezone(UTC).isoformat(timespec="microseconds")


def _latest_file(root: Path, pattern: str) -> Path:
    matches = [path for path in root.glob(pattern) if path.is_file()]
    if not matches:
        raise FileNotFoundError(f"required report not found: {root / pattern}")
    return max(matches, key=lambda path: path.stat().st_mtime)


def discover_early_failure_rows(
    root: Path,
    *,
    dev_results_path: Path | None = None,
    holdout_features_path: Path | None = None,
) -> dict[tuple[str, str], str]:
    dev_path = dev_results_path or _latest_file(
        root,
        "reports/core_runner_split_v1/UNI_LINK_*/policy_results.csv",
    )
    holdout_path = holdout_features_path or _latest_file(
        root,
        "reports/hourly_trend_oos_v1/HOLDOUT7_*/hourly_features.csv",
    )

    selected: dict[tuple[str, str], str] = {}
    dev_count = 0
    with dev_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("policy_id") != "CORE100_TAKE_1P00":
                continue
            if row.get("exit_reason") != "initial_stop":
                continue
            symbol = str(row["symbol"])
            touch_at = _parse_datetime(str(row["touch_at"]))
            selected[_key(symbol, touch_at)] = "development"
            dev_count += 1

    holdout_count = 0
    with holdout_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("exit_reason") != "initial_stop":
                continue
            symbol = str(row["symbol"])
            touch_at = _parse_datetime(str(row["touch_at"]))
            selected[_key(symbol, touch_at)] = "holdout"
            holdout_count += 1

    if dev_count != EXPECTED_DEV_FAILURES:
        raise ValueError(
            f"development early-failure count mismatch: {dev_count} != {EXPECTED_DEV_FAILURES}"
        )
    if holdout_count != EXPECTED_HOLDOUT_FAILURES:
        raise ValueError(
            f"holdout early-failure count mismatch: "
            f"{holdout_count} != {EXPECTED_HOLDOUT_FAILURES}"
        )
    if len(selected) != EXPECTED_EARLY_FAILURES:
        raise ValueError(
            f"pooled early-failure count mismatch: {len(selected)} != {EXPECTED_EARLY_FAILURES}"
        )
    return selected


def validation_p40(root: Path, symbol: str) -> Path:
    return root / "reports" / "cross_asset_validation" / f"{symbol}_{PERIOD_TAG}" / "p40"


def discover_sources(root: Path) -> tuple[SignalSource, ...]:
    result: list[SignalSource] = []
    for symbol in ALL_SYMBOLS:
        source = discover_source(validation_p40(root, symbol))
        if source.symbol != symbol:
            raise ValueError(f"P40 symbol mismatch: expected {symbol}, got {source.symbol}")
        result.append(source)
    return tuple(result)


def load_selected_signals(
    sources: tuple[SignalSource, ...],
    selected_rows: dict[tuple[str, str], str],
) -> tuple[tuple[CoreSignal, str], ...]:
    found: dict[tuple[str, str], CoreSignal] = {}
    for source in sources:
        for signal in load_core_signals(source):
            signal_key = _key(signal.symbol, signal.touch_at)
            if signal_key in selected_rows:
                found[signal_key] = signal

    missing = sorted(set(selected_rows) - set(found))
    if missing:
        preview = ", ".join(f"{symbol}@{touch}" for symbol, touch in missing[:5])
        raise ValueError(f"could not match {len(missing)} early failures to P40 signals: {preview}")

    items = [
        (found[signal_key], selected_rows[signal_key])
        for signal_key in selected_rows
    ]
    return tuple(sorted(items, key=lambda item: (item[0].symbol, item[0].touch_at)))


def _first_index_at_or_below(path: PathSeries, threshold: float, start: int = 0) -> int | None:
    for index in range(start, len(path.moves_pct)):
        if path.moves_pct[index] <= threshold:
            return index
    return None


def _first_index_at_or_above(path: PathSeries, threshold: float, start: int = 0) -> int | None:
    for index in range(start, len(path.moves_pct)):
        if path.moves_pct[index] >= threshold:
            return index
    return None


def _seconds_between(path: PathSeries, start_index: int, end_index: int) -> float:
    return max(0.0, path.timestamps[end_index] - path.timestamps[start_index])


def classify_early_failure(
    path: PathSeries,
    source_scope: str,
    config: PunctureConfig,
) -> EarlyFailureEvent:
    if not path.moves_pct:
        raise ValueError(f"empty path for {path.signal.symbol} {path.signal.touch_at.isoformat()}")

    minus_1_index = _first_index_at_or_below(path, -config.initial_stop_pct)
    if minus_1_index is None:
        raise ValueError(
            f"selected early failure never reaches -{config.initial_stop_pct}%: "
            f"{path.signal.symbol} {path.signal.touch_at.isoformat()}"
        )

    near_index = _first_index_at_or_above(
        path,
        config.near_zone_level_pct,
        start=minus_1_index + 1,
    )
    recovery_index = _first_index_at_or_above(
        path,
        config.entry_recovery_level_pct,
        start=minus_1_index + 1,
    )
    minus_3_index = _first_index_at_or_below(
        path,
        -config.deep_break_pct,
        start=minus_1_index + 1,
    )

    recross_minus_1_index = _first_index_at_or_above(
        path,
        -config.initial_stop_pct,
        start=minus_1_index + 1,
    )

    if recovery_index is not None and (
        minus_3_index is None or recovery_index < minus_3_index
    ):
        class_name: FailureClass = "puncture_recovered_before_3"
    elif minus_3_index is not None:
        later_recovery = _first_index_at_or_above(
            path,
            config.entry_recovery_level_pct,
            start=minus_3_index + 1,
        )
        class_name = (
            "deep_3_then_recovered" if later_recovery is not None else "deep_3_no_recovery"
        )
    else:
        class_name = "no_recovery_no_3"

    end_before_recovery = recovery_index if recovery_index is not None else len(path.moves_pct)
    adverse_slice = path.moves_pct[minus_1_index:end_before_recovery]
    deepest_before_recovery = min(adverse_slice) if adverse_slice else path.moves_pct[minus_1_index]
    deepest_72h = min(path.moves_pct)
    start_ts = path.signal.touch_at.timestamp()
    minus_1_ts = path.timestamps[minus_1_index]

    def at(index: int | None) -> str | None:
        if index is None:
            return None
        return datetime.fromtimestamp(path.timestamps[index], UTC).isoformat()

    def since_stop(index: int | None) -> float | None:
        if index is None:
            return None
        return _seconds_between(path, minus_1_index, index)

    required_until = path.signal.touch_at.timestamp() + config.horizon_hours * 3600
    completed = path.complete_through.timestamp() >= required_until

    return EarlyFailureEvent(
        symbol=path.signal.symbol,
        direction=str(path.signal.direction),
        touch_at=path.signal.touch_at.isoformat(),
        entry_price=path.signal.entry_price,
        source_scope=source_scope,
        first_minus_1_at=at(minus_1_index) or "",
        first_minus_1_seconds=max(0.0, minus_1_ts - start_ts),
        first_near_zone_at=at(near_index),
        seconds_to_near_zone=since_stop(near_index),
        first_entry_recovery_at=at(recovery_index),
        seconds_to_entry_recovery=since_stop(recovery_index),
        first_minus_3_at=at(minus_3_index),
        seconds_to_minus_3=(
            max(0.0, path.timestamps[minus_3_index] - start_ts)
            if minus_3_index is not None
            else None
        ),
        seconds_minus_1_to_minus_3=since_stop(minus_3_index),
        continuous_below_minus_1_seconds=since_stop(recross_minus_1_index),
        deepest_move_72h_pct=deepest_72h,
        deepest_before_entry_recovery_pct=deepest_before_recovery,
        recovered_near_zone_72h=near_index is not None,
        recovered_entry_72h=recovery_index is not None,
        hit_minus_3_72h=minus_3_index is not None,
        hit_minus_3_before_entry_recovery=(
            minus_3_index is not None
            and (recovery_index is None or minus_3_index < recovery_index)
        ),
        class_name=class_name,
        completed_72h=completed,
        missing_archive_days=";".join(path.missing_archive_days),
    )


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _time_buckets(values: list[float | None]) -> dict[str, int]:
    result = {
        "<=5s": 0,
        "5-30s": 0,
        "30-60s": 0,
        "1-5m": 0,
        "5-15m": 0,
        "15-60m": 0,
        ">1h": 0,
        "no_recovery": 0,
    }
    for value in values:
        if value is None:
            result["no_recovery"] += 1
        elif value <= 5:
            result["<=5s"] += 1
        elif value <= 30:
            result["5-30s"] += 1
        elif value <= 60:
            result["30-60s"] += 1
        elif value <= 300:
            result["1-5m"] += 1
        elif value <= 900:
            result["5-15m"] += 1
        elif value <= 3600:
            result["15-60m"] += 1
        else:
            result[">1h"] += 1
    return result


def summarize(events: tuple[EarlyFailureEvent, ...]) -> dict[str, Any]:
    class_counts: dict[str, int] = {}
    asset_counts: dict[str, dict[str, int]] = {}
    for event in events:
        class_counts[event.class_name] = class_counts.get(event.class_name, 0) + 1
        asset = asset_counts.setdefault(
            event.symbol,
            {
                "signals": 0,
                "recovered_entry": 0,
                "hit_minus_3": 0,
                "deep_3_no_recovery": 0,
                "puncture_recovered_before_3": 0,
            },
        )
        asset["signals"] += 1
        asset["recovered_entry"] += int(event.recovered_entry_72h)
        asset["hit_minus_3"] += int(event.hit_minus_3_72h)
        asset["deep_3_no_recovery"] += int(event.class_name == "deep_3_no_recovery")
        asset["puncture_recovered_before_3"] += int(
            event.class_name == "puncture_recovered_before_3"
        )

    recovery_times = [
        event.seconds_to_entry_recovery
        for event in events
        if event.seconds_to_entry_recovery is not None
    ]
    below_times = [
        event.continuous_below_minus_1_seconds
        for event in events
        if event.continuous_below_minus_1_seconds is not None
    ]
    to_minus_3 = [
        event.seconds_minus_1_to_minus_3
        for event in events
        if event.seconds_minus_1_to_minus_3 is not None
    ]

    total = len(events)
    recovered_entry = sum(event.recovered_entry_72h for event in events)
    recovered_near = sum(event.recovered_near_zone_72h for event in events)
    hit_minus_3 = sum(event.hit_minus_3_72h for event in events)
    true_breaks = class_counts.get("deep_3_no_recovery", 0)
    return {
        "signals": total,
        "development": sum(event.source_scope == "development" for event in events),
        "holdout": sum(event.source_scope == "holdout" for event in events),
        "completed_72h": sum(event.completed_72h for event in events),
        "recovered_near_zone": recovered_near,
        "recovered_near_zone_pct": 100.0 * recovered_near / total,
        "recovered_entry": recovered_entry,
        "recovered_entry_pct": 100.0 * recovered_entry / total,
        "no_entry_recovery": total - recovered_entry,
        "hit_minus_3": hit_minus_3,
        "hit_minus_3_pct": 100.0 * hit_minus_3 / total,
        "true_break_minus_3_no_recovery": true_breaks,
        "true_break_minus_3_no_recovery_pct": 100.0 * true_breaks / total,
        "classes": class_counts,
        "entry_recovery_time_buckets": _time_buckets(
            [event.seconds_to_entry_recovery for event in events]
        ),
        "entry_recovery_seconds_median": _median(recovery_times),
        "entry_recovery_seconds_p25": _percentile(recovery_times, 0.25),
        "entry_recovery_seconds_p75": _percentile(recovery_times, 0.75),
        "continuous_below_minus_1_seconds_median": _median(below_times),
        "minus_1_to_minus_3_seconds_median": _median(to_minus_3),
        "deepest_move_72h_pct_median": _median(
            [event.deepest_move_72h_pct for event in events]
        ),
        "assets": asset_counts,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(path: Path, summary: dict[str, Any]) -> None:
    classes = summary["classes"]
    lines = [
        "# P47G Early Failure Puncture Anatomy",
        "",
        f"Early failures: **{summary['signals']}**",
        f"Development / holdout: {summary['development']} / {summary['holdout']}",
        "",
        "## Primary answer",
        "",
        f"- Returned to near-entry zone (-0.10%): **{summary['recovered_near_zone']}** "
        f"({summary['recovered_near_zone_pct']:.2f}%)",
        f"- Returned through Entry (0.00%): **{summary['recovered_entry']}** "
        f"({summary['recovered_entry_pct']:.2f}%)",
        f"- Reached -3.00% at any point: **{summary['hit_minus_3']}** "
        f"({summary['hit_minus_3_pct']:.2f}%)",
        f"- Reached -3.00% and never returned to Entry in 72h: "
        f"**{summary['true_break_minus_3_no_recovery']}** "
        f"({summary['true_break_minus_3_no_recovery_pct']:.2f}%)",
        "",
        "## Four-way classification",
        "",
        f"- Puncture, recovered before -3%: "
        f"**{classes.get('puncture_recovered_before_3', 0)}**",
        f"- Hit -3%, later recovered to Entry: "
        f"**{classes.get('deep_3_then_recovered', 0)}**",
        f"- Hit -3%, no Entry recovery: **{classes.get('deep_3_no_recovery', 0)}**",
        f"- No Entry recovery, but did not hit -3%: "
        f"**{classes.get('no_recovery_no_3', 0)}**",
        "",
        "## Timing",
        "",
        f"Median -1% -> Entry recovery: "
        f"{summary['entry_recovery_seconds_median']} seconds",
        f"Median continuous time below -1%: "
        f"{summary['continuous_below_minus_1_seconds_median']} seconds",
        f"Median -1% -> -3% among deep breaks: "
        f"{summary['minus_1_to_minus_3_seconds_median']} seconds",
        "",
        "The analysis is direction-normalized: LONG and SHORT are treated symmetrically.",
        "A -3% move means 3% adverse price movement relative to the Entry direction.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    root: Path,
    output_dir: Path,
    config: PunctureConfig,
    *,
    dev_results_path: Path | None = None,
    holdout_features_path: Path | None = None,
) -> dict[str, Any]:
    selected_rows = discover_early_failure_rows(
        root,
        dev_results_path=dev_results_path,
        holdout_features_path=holdout_features_path,
    )
    sources = discover_sources(root)
    signals = load_selected_signals(sources, selected_rows)
    if len(signals) != EXPECTED_EARLY_FAILURES:
        raise ValueError(f"expected {EXPECTED_EARLY_FAILURES} signals, got {len(signals)}")

    source_by_symbol = {source.symbol: source for source in sources}
    cache_by_symbol = {
        symbol: TradeDayCache(max_days=config.day_cache_size) for symbol in ALL_SYMBOLS
    }
    archive_by_symbol = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    reporter = ProgressReporter(config.progress_interval_seconds)
    reporter.emit(processed=0, total=len(signals), force=True, detail="66 early failures only")

    events: list[EarlyFailureEvent] = []
    for index, (signal, scope) in enumerate(signals, start=1):
        path = build_path_series(
            signal,
            archive_by_symbol[signal.symbol],
            horizon_hours=config.horizon_hours,
            cache=cache_by_symbol[signal.symbol],
        )
        event = classify_early_failure(path, scope, config)
        events.append(event)
        cache = cache_by_symbol[signal.symbol]
        reporter.emit(
            processed=index,
            total=len(signals),
            detail=(
                f"symbol={signal.symbol} class={event.class_name} "
                f"cache_hits={cache.hits} cache_misses={cache.misses}"
            ),
        )

    events_tuple = tuple(events)
    summary = summarize(events_tuple)
    summary["config"] = asdict(config)
    summary["generated_at"] = datetime.now(UTC).isoformat()

    _write_csv(output_dir / "early_failure_events.csv", [asdict(event) for event in events_tuple])
    asset_rows = []
    for symbol in ALL_SYMBOLS:
        row = {"symbol": symbol}
        row.update(summary["assets"].get(symbol, {}))
        asset_rows.append(row)
    _write_csv(output_dir / "asset_summary.csv", asset_rows)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_summary_md(output_dir / "summary.md", summary)
    reporter.emit(processed=len(signals), total=len(signals), force=True, detail="done")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="P47G early failure puncture anatomy")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--horizon-hours", type=int, default=72)
    parser.add_argument("--deep-break-pct", type=float, default=3.0)
    parser.add_argument("--near-zone-level-pct", type=float, default=-0.10)
    parser.add_argument("--day-cache-size", type=int, default=6)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    args = parser.parse_args()

    root = args.root.resolve()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "early_failure_puncture_v1" / f"ALL9_{stamp}"

    config = PunctureConfig(
        deep_break_pct=args.deep_break_pct,
        near_zone_level_pct=args.near_zone_level_pct,
        horizon_hours=args.horizon_hours,
        day_cache_size=args.day_cache_size,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    summary = run_research(root, output_dir.resolve(), config)
    print(f"P47G early failures: {summary['signals']}")
    print(
        "P47G recovered near-zone / entry: "
        f"{summary['recovered_near_zone']} / {summary['recovered_entry']}"
    )
    print(
        "P47G hit -3 / true -3 no-recovery: "
        f"{summary['hit_minus_3']} / {summary['true_break_minus_3_no_recovery']}"
    )
    print(f"Report: {output_dir.resolve() / 'summary.json'}")
    print(f"Readable summary: {output_dir.resolve() / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
