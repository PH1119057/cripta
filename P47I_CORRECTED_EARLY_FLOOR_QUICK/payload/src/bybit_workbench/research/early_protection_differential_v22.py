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
BASELINE_RUNNER = "B_FULL_RUNNER_MFE_GB1P50"
EXPECTED_SIMPLE_REASONS = {
    "initial_stop": 66,
    "early_be": 851,
    "core_take": 142,
    "data_end": 4,
}
EXPECTED_RUNNER_REASONS = {
    "initial_stop": 66,
    "early_be": 851,
    "runner_stop": 141,
    "horizon": 1,
    "data_end": 4,
}
EXPECTED_SIMPLE_GROSS = 76.065635
EXPECTED_RUNNER_GROSS = 95.114436

Outcome = Literal["new_floor_stop", "reached_1p10", "data_end"]


@dataclass(frozen=True, slots=True)
class QuickConfig:
    initial_stop_pct: float = 1.0
    activation_pct: float = 0.10
    corrected_floor_pct: float = 0.10
    runner_activation_pct: float = 1.10
    horizon_hours: int = 72
    day_cache_size: int = 4
    progress_interval_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.initial_stop_pct <= 0:
            raise ValueError("initial_stop_pct must be positive")
        if self.activation_pct <= 0:
            raise ValueError("activation_pct must be positive")
        if self.corrected_floor_pct != self.activation_pct:
            raise ValueError("this audit requires corrected floor == activation")
        if self.runner_activation_pct <= self.activation_pct:
            raise ValueError("runner activation must be above early activation")
        if self.horizon_hours <= 0:
            raise ValueError("horizon_hours must be positive")
        if self.day_cache_size <= 0:
            raise ValueError("day_cache_size must be positive")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


@dataclass(frozen=True, slots=True)
class BaselineRow:
    symbol: str
    touch_at: datetime
    policy_id: str
    exit_reason: str
    exit_move_pct: float


@dataclass(frozen=True, slots=True)
class DifferentialResult:
    symbol: str
    touch_at: datetime
    baseline_simple_reason: str
    baseline_simple_exit_pct: float
    baseline_runner_reason: str
    baseline_runner_exit_pct: float
    outcome: Outcome
    activation_at: datetime | None
    event_at: datetime | None
    seconds_activation_to_event: float | None
    same_timestamp_event: bool
    corrected_simple_exit_pct: float
    corrected_runner_exit_pct: float


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
            f"[P47I] processed={processed}/{total} "
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
    candidates = sorted(
        (path for path in report_root.glob("ALL9_*") if path.is_dir()),
        key=lambda path: path.name,
    )
    if not candidates:
        raise FileNotFoundError(f"no P47H ALL9 report found under {report_root}")
    return candidates[-1]


def _load_baseline_rows(path: Path) -> tuple[BaselineRow, ...]:
    rows: list[BaselineRow] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            policy_id = str(row.get("policy_id") or "")
            if policy_id not in {BASELINE_SIMPLE, BASELINE_RUNNER}:
                continue
            rows.append(
                BaselineRow(
                    symbol=str(row.get("symbol") or ""),
                    touch_at=datetime.fromisoformat(str(row.get("touch_at") or "")).astimezone(UTC),
                    policy_id=policy_id,
                    exit_reason=str(row.get("exit_reason") or ""),
                    exit_move_pct=float(row.get("exit_move_pct") or 0.0),
                )
            )
    return tuple(rows)


def _reason_counts(rows: tuple[BaselineRow, ...], policy_id: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.policy_id == policy_id:
            counts[row.exit_reason] = counts.get(row.exit_reason, 0) + 1
    return counts


def validate_baseline(rows: tuple[BaselineRow, ...]) -> None:
    simple = tuple(row for row in rows if row.policy_id == BASELINE_SIMPLE)
    runner = tuple(row for row in rows if row.policy_id == BASELINE_RUNNER)
    if len(simple) != EXPECTED_ALL9 or len(runner) != EXPECTED_ALL9:
        raise ValueError(
            f"P47H baseline row count mismatch: simple={len(simple)} runner={len(runner)}"
        )
    simple_counts = _reason_counts(rows, BASELINE_SIMPLE)
    runner_counts = _reason_counts(rows, BASELINE_RUNNER)
    if simple_counts != EXPECTED_SIMPLE_REASONS:
        raise ValueError(
            f"P47H simple reason guardrail failed: {simple_counts} != "
            f"{EXPECTED_SIMPLE_REASONS}"
        )
    if runner_counts != EXPECTED_RUNNER_REASONS:
        raise ValueError(
            f"P47H runner reason guardrail failed: {runner_counts} != "
            f"{EXPECTED_RUNNER_REASONS}"
        )
    simple_gross = sum(row.exit_move_pct for row in simple)
    runner_gross = sum(row.exit_move_pct for row in runner)
    if abs(simple_gross - EXPECTED_SIMPLE_GROSS) > 1e-6:
        raise ValueError(
            f"P47H simple gross guardrail failed: {simple_gross:.6f} != "
            f"{EXPECTED_SIMPLE_GROSS:.6f}"
        )
    if abs(runner_gross - EXPECTED_RUNNER_GROSS) > 1e-6:
        raise ValueError(
            f"P47H runner gross guardrail failed: {runner_gross:.6f} != "
            f"{EXPECTED_RUNNER_GROSS:.6f}"
        )


def _baseline_map(
    rows: tuple[BaselineRow, ...],
) -> dict[tuple[str, datetime], dict[str, BaselineRow]]:
    result: dict[tuple[str, datetime], dict[str, BaselineRow]] = {}
    for row in rows:
        key = (row.symbol, row.touch_at)
        result.setdefault(key, {})[row.policy_id] = row
    for key, policies in result.items():
        if set(policies) != {BASELINE_SIMPLE, BASELINE_RUNNER}:
            raise ValueError(f"baseline policy pair missing for {key}: {sorted(policies)}")
    return result


def _signal_map(sources: tuple[SignalSource, ...]) -> dict[tuple[str, datetime], CoreSignal]:
    result: dict[tuple[str, datetime], CoreSignal] = {}
    counts: dict[str, int] = {}
    for source in sources:
        signals = load_core_signals(source)
        counts[source.symbol] = len(signals)
        for signal in signals:
            result[(signal.symbol, signal.touch_at)] = signal
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"frozen P40 signal guardrail failed: {counts} != {EXPECTED_COUNTS}")
    if len(result) != EXPECTED_ALL9:
        raise ValueError(f"expected {EXPECTED_ALL9} unique signals, got {len(result)}")
    return result


def _date_strings(start: datetime, hours: int) -> tuple[str, ...]:
    end = start + timedelta(hours=hours)
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    days: list[str] = []
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return tuple(days)


def scan_corrected_floor(
    signal: CoreSignal,
    archive_by_day: dict[str, Path],
    cache: TradeDayCache,
    config: QuickConfig,
) -> tuple[Outcome, datetime | None, datetime | None, float | None, bool]:
    start_ts = signal.touch_at.timestamp()
    end_ts = (signal.touch_at + timedelta(hours=config.horizon_hours)).timestamp()
    activated = False
    activation_ts: float | None = None

    for day in _date_strings(signal.touch_at, config.horizon_hours):
        archive = archive_by_day.get(day)
        if archive is None:
            break
        tape = cache.get(archive)
        start_index = bisect.bisect_left(tape.timestamps, start_ts)
        end_index = bisect.bisect_right(tape.timestamps, end_ts)
        for index in range(start_index, end_index):
            timestamp = tape.timestamps[index]
            move = directional_move_pct(
                signal.direction,
                signal.entry_price,
                tape.prices[index],
            )
            if not activated:
                if move <= -config.initial_stop_pct:
                    raise ValueError(
                        "candidate unexpectedly hits initial stop before activation: "
                        f"{signal.symbol} {signal.touch_at.isoformat()}"
                    )
                if move >= config.activation_pct:
                    activated = True
                    activation_ts = timestamp
                continue

            if move <= config.corrected_floor_pct:
                if activation_ts is None:
                    raise RuntimeError("activation timestamp missing")
                event_at = datetime.fromtimestamp(timestamp, UTC)
                activation_at = datetime.fromtimestamp(activation_ts, UTC)
                return (
                    "new_floor_stop",
                    activation_at,
                    event_at,
                    max(0.0, timestamp - activation_ts),
                    timestamp == activation_ts,
                )
            if move >= config.runner_activation_pct:
                if activation_ts is None:
                    raise RuntimeError("activation timestamp missing")
                event_at = datetime.fromtimestamp(timestamp, UTC)
                activation_at = datetime.fromtimestamp(activation_ts, UTC)
                return (
                    "reached_1p10",
                    activation_at,
                    event_at,
                    max(0.0, timestamp - activation_ts),
                    timestamp == activation_ts,
                )

    activation_at = None if activation_ts is None else datetime.fromtimestamp(activation_ts, UTC)
    return "data_end", activation_at, None, None, False


def _corrected_exit(
    simple: BaselineRow,
    runner: BaselineRow,
    outcome: Outcome | None,
    config: QuickConfig,
) -> tuple[float, float]:
    if simple.exit_reason == "initial_stop":
        return simple.exit_move_pct, runner.exit_move_pct
    if simple.exit_reason == "early_be":
        return config.corrected_floor_pct, config.corrected_floor_pct
    if simple.exit_reason in {"core_take", "data_end"}:
        if outcome == "new_floor_stop":
            return config.corrected_floor_pct, config.corrected_floor_pct
        return simple.exit_move_pct, runner.exit_move_pct
    raise ValueError(f"unexpected baseline simple reason: {simple.exit_reason}")


def _scope(symbol: str) -> str:
    return "DEV2" if symbol in DEV_SYMBOLS else "HOLDOUT7"


def _median(values: list[float]) -> float | None:
    return round(float(statistics.median(values)), 6) if values else None


def _summarize(
    baseline: dict[tuple[str, datetime], dict[str, BaselineRow]],
    differential: dict[tuple[str, datetime], DifferentialResult],
    scope_name: str,
    config: QuickConfig,
) -> dict[str, Any]:
    if scope_name == "ALL9":
        keys = list(baseline)
    elif scope_name == "DEV2":
        keys = [key for key in baseline if key[0] in DEV_SYMBOLS]
    elif scope_name == "HOLDOUT7":
        keys = [key for key in baseline if key[0] in HOLDOUT_SYMBOLS]
    else:
        keys = [key for key in baseline if key[0] == scope_name]

    old_simple = [baseline[key][BASELINE_SIMPLE].exit_move_pct for key in keys]
    old_runner = [baseline[key][BASELINE_RUNNER].exit_move_pct for key in keys]
    corrected_simple: list[float] = []
    corrected_runner: list[float] = []
    lost_runner_keys: list[tuple[str, datetime]] = []
    candidate_results: list[DifferentialResult] = []

    for key in keys:
        simple = baseline[key][BASELINE_SIMPLE]
        runner = baseline[key][BASELINE_RUNNER]
        diff = differential.get(key)
        outcome = None if diff is None else diff.outcome
        simple_exit, runner_exit = _corrected_exit(simple, runner, outcome, config)
        corrected_simple.append(simple_exit)
        corrected_runner.append(runner_exit)
        if diff is not None:
            candidate_results.append(diff)
            if simple.exit_reason == "core_take" and diff.outcome == "new_floor_stop":
                lost_runner_keys.append(key)

    old_runner_candidates = sum(
        baseline[key][BASELINE_SIMPLE].exit_reason == "core_take" for key in keys
    )
    retained_runner_candidates = old_runner_candidates - len(lost_runner_keys)
    stop_delays = [
        item.seconds_activation_to_event
        for item in candidate_results
        if item.outcome == "new_floor_stop" and item.seconds_activation_to_event is not None
    ]
    same_timestamp = sum(
        item.outcome == "new_floor_stop" and item.same_timestamp_event
        for item in candidate_results
    )
    return {
        "scope": scope_name,
        "signals": len(keys),
        "baseline_simple_gross_pct": round(sum(old_simple), 6),
        "corrected_simple_gross_pct": round(sum(corrected_simple), 6),
        "simple_delta_pct": round(sum(corrected_simple) - sum(old_simple), 6),
        "baseline_runner_gross_pct": round(sum(old_runner), 6),
        "corrected_runner_gross_pct": round(sum(corrected_runner), 6),
        "runner_delta_pct": round(sum(corrected_runner) - sum(old_runner), 6),
        "old_runner_candidates": old_runner_candidates,
        "retained_runner_candidates": retained_runner_candidates,
        "lost_runner_candidates": len(lost_runner_keys),
        "runner_retention_pct": (
            round(retained_runner_candidates * 100.0 / old_runner_candidates, 6)
            if old_runner_candidates
            else None
        ),
        "candidate_new_floor_stops": sum(
            item.outcome == "new_floor_stop" for item in candidate_results
        ),
        "candidate_reached_1p10": sum(
            item.outcome == "reached_1p10" for item in candidate_results
        ),
        "candidate_data_end": sum(item.outcome == "data_end" for item in candidate_results),
        "median_activation_to_new_stop_seconds": _median(
            [float(value) for value in stop_delays]
        ),
        "same_timestamp_new_stops": same_timestamp,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(
    path: Path,
    *,
    baseline_dir: Path,
    config: QuickConfig,
    summaries: list[dict[str, Any]],
    candidates: int,
) -> None:
    all9 = next(row for row in summaries if row["scope"] == "ALL9")
    lines = [
        "# P47I Corrected +0.10 Early Floor - Quick Differential",
        "",
        "## Contract",
        "",
        "- Frozen Entry V1 and P46 are unchanged.",
        "- Historical P47H baseline used +0.10 activation -> 0.00 theoretical floor.",
        "- Corrected test uses +0.10 activation -> +0.10 theoretical floor.",
        "- Once +1.10 is reached, the old downstream exit result is reused unchanged.",
        "- Fees/slippage are not netted; +0.10 is a price-level theoretical floor here.",
        "- Downloads: DISABLED. Existing frozen public-trade archives only.",
        "- Only P47H core-take/data-end candidates are rescanned, not all 1063 72h paths.",
        "",
        f"Baseline: `{baseline_dir}`",
        f"Candidates rescanned: **{candidates}**",
        "",
        "## ALL9",
        "",
        f"- Old runner candidates (+1.10 reached): **{all9['old_runner_candidates']}**",
        f"- Retained with corrected +0.10 floor: **{all9['retained_runner_candidates']}**",
        f"- Lost before +1.10: **{all9['lost_runner_candidates']}**",
        f"- Runner retention: **{all9['runner_retention_pct']}%**",
        f"- Full Runner gross old: **{all9['baseline_runner_gross_pct']}%**",
        f"- Full Runner gross corrected: **{all9['corrected_runner_gross_pct']}%**",
        f"- Full Runner delta: **{all9['runner_delta_pct']} pp**",
        f"- Simple gross old: **{all9['baseline_simple_gross_pct']}%**",
        f"- Simple gross corrected: **{all9['corrected_simple_gross_pct']}%**",
        f"- Same-timestamp corrected-floor events: **{all9['same_timestamp_new_stops']}**",
        "",
        "## Scope table",
        "",
        "| scope | signals | old runners | retained | lost | retention % | "
        "old runner gross | corrected runner gross | delta |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summaries:
        lines.append(
            f"| {row['scope']} | {row['signals']} | {row['old_runner_candidates']} | "
            f"{row['retained_runner_candidates']} | {row['lost_runner_candidates']} | "
            f"{row['runner_retention_pct']} | {row['baseline_runner_gross_pct']} | "
            f"{row['corrected_runner_gross_pct']} | {row['runner_delta_pct']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrail",
            "",
            "This is a fast differential audit of the corrected early floor, not a new "
            "parameter search.",
            "Do not retune thresholds on these same samples. Same-timestamp/near-instant "
            "events are flagged",
            "because real stop placement latency can make sub-second historical ordering "
            "execution-sensitive.",
            "",
            "The separate Entry statement '+0.10 before -1.00' remains a first-touch "
            "ordering metric only;",
            "it does not describe oscillations, adverse excursions or time spent below "
            "Entry before +0.10.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(
    root: Path,
    *,
    baseline_dir: Path | None = None,
    output_dir: Path | None = None,
    config: QuickConfig | None = None,
) -> Path:
    cfg = config or QuickConfig()
    baseline_root = baseline_dir or discover_latest_p47h(root)
    baseline_csv = baseline_root / "policy_results.csv"
    if not baseline_csv.exists():
        raise FileNotFoundError(f"P47H policy_results.csv missing: {baseline_csv}")

    baseline_rows = _load_baseline_rows(baseline_csv)
    validate_baseline(baseline_rows)
    baseline = _baseline_map(baseline_rows)
    sources = discover_sources(root)
    signals = _signal_map(sources)
    source_by_symbol = {source.symbol: source for source in sources}

    candidate_keys = sorted(
        (
            key
            for key, policies in baseline.items()
            if policies[BASELINE_SIMPLE].exit_reason in {"core_take", "data_end"}
        ),
        key=lambda key: (key[0], key[1]),
    )
    expected_candidates = EXPECTED_SIMPLE_REASONS["core_take"] + EXPECTED_SIMPLE_REASONS["data_end"]
    if len(candidate_keys) != expected_candidates:
        raise ValueError(
            f"expected {expected_candidates} differential candidates, got {len(candidate_keys)}"
        )

    reporter = ProgressReporter(cfg.progress_interval_seconds)
    reporter.emit(
        processed=0,
        total=len(candidate_keys),
        force=True,
        detail="fast rescan only; no 72h runner replay",
    )
    caches = {
        symbol: TradeDayCache(max_days=cfg.day_cache_size) for symbol in ALL_SYMBOLS
    }
    archives = {
        symbol: _archive_map(source_by_symbol[symbol].dataset_dir) for symbol in ALL_SYMBOLS
    }
    differential: dict[tuple[str, datetime], DifferentialResult] = {}

    for index, key in enumerate(candidate_keys, start=1):
        signal = signals.get(key)
        if signal is None:
            raise ValueError(f"candidate is absent from frozen P40 signals: {key}")
        simple = baseline[key][BASELINE_SIMPLE]
        runner = baseline[key][BASELINE_RUNNER]
        outcome, activation_at, event_at, delay, same_timestamp = scan_corrected_floor(
            signal,
            archives[signal.symbol],
            caches[signal.symbol],
            cfg,
        )
        simple_exit, runner_exit = _corrected_exit(simple, runner, outcome, cfg)
        differential[key] = DifferentialResult(
            symbol=signal.symbol,
            touch_at=signal.touch_at,
            baseline_simple_reason=simple.exit_reason,
            baseline_simple_exit_pct=simple.exit_move_pct,
            baseline_runner_reason=runner.exit_reason,
            baseline_runner_exit_pct=runner.exit_move_pct,
            outcome=outcome,
            activation_at=activation_at,
            event_at=event_at,
            seconds_activation_to_event=delay,
            same_timestamp_event=same_timestamp,
            corrected_simple_exit_pct=simple_exit,
            corrected_runner_exit_pct=runner_exit,
        )
        reporter.emit(
            processed=index,
            total=len(candidate_keys),
            detail=f"symbol={signal.symbol} outcome={outcome}",
        )

    for key, item in differential.items():
        baseline_reason = baseline[key][BASELINE_SIMPLE].exit_reason
        if baseline_reason == "core_take" and item.outcome == "data_end":
            raise ValueError(f"core-take candidate did not resolve in quick scan: {key}")
        if baseline_reason == "data_end" and item.outcome == "reached_1p10":
            raise ValueError(f"data-end candidate unexpectedly reached +1.10: {key}")

    reporter.emit(
        processed=len(candidate_keys),
        total=len(candidate_keys),
        force=True,
        detail="done",
    )

    scopes = ["ALL9", "DEV2", "HOLDOUT7", *ALL_SYMBOLS]
    summaries = [_summarize(baseline, differential, scope, cfg) for scope in scopes]

    if output_dir is None:
        stamp = datetime.now(UTC).strftime("ALL9_%Y%m%d_%H%M%S")
        output_dir = root / "reports" / "early_protection_differential_v1" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    candidate_rows: list[dict[str, Any]] = []
    for item in differential.values():
        row = asdict(item)
        for key in ("touch_at", "activation_at", "event_at"):
            value = row[key]
            row[key] = value.isoformat() if isinstance(value, datetime) else ""
        candidate_rows.append(row)
    candidate_rows.sort(key=lambda row: (str(row["symbol"]), str(row["touch_at"])))
    _write_csv(output_dir / "candidate_differential.csv", candidate_rows)
    _write_csv(output_dir / "scope_summary.csv", summaries)

    payload = {
        "research": "P47I corrected early protection quick differential",
        "created_at": datetime.now(UTC).isoformat(),
        "baseline_dir": str(baseline_root),
        "baseline_guardrails": {
            "signals": EXPECTED_ALL9,
            "simple_reason_counts": EXPECTED_SIMPLE_REASONS,
            "runner_reason_counts": EXPECTED_RUNNER_REASONS,
            "simple_gross_pct": EXPECTED_SIMPLE_GROSS,
            "runner_gross_pct": EXPECTED_RUNNER_GROSS,
        },
        "config": asdict(cfg),
        "candidates_rescanned": len(candidate_keys),
        "downloads": "DISABLED / fail-closed",
        "semantics": {
            "old": "+0.10 activation -> 0.00 theoretical floor",
            "corrected": "+0.10 activation -> +0.10 theoretical floor",
            "after_1p10": "reuse frozen P47H downstream result unchanged",
            "fees_slippage": "not netted in this differential",
        },
        "scope_summary": summaries,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_summary_md(
        output_dir / "summary.md",
        baseline_dir=baseline_root,
        config=cfg,
        summaries=summaries,
        candidates=len(candidate_keys),
    )

    all9 = next(row for row in summaries if row["scope"] == "ALL9")
    print(
        "P47I ALL9: "
        f"old_runners={all9['old_runner_candidates']} "
        f"retained={all9['retained_runner_candidates']} "
        f"lost={all9['lost_runner_candidates']} "
        f"retention={all9['runner_retention_pct']}%",
        flush=True,
    )
    print(
        "P47I Full Runner gross: "
        f"old={all9['baseline_runner_gross_pct']}% "
        f"corrected={all9['corrected_runner_gross_pct']}% "
        f"delta={all9['runner_delta_pct']}pp",
        flush=True,
    )
    print(f"Report: {output_dir}", flush=True)
    return output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P47I quick differential for corrected +0.10 early protection floor."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--progress-interval-seconds", type=float, default=20.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = QuickConfig(progress_interval_seconds=args.progress_interval_seconds)
    run(
        args.project_root.resolve(),
        baseline_dir=None if args.baseline_dir is None else args.baseline_dir.resolve(),
        output_dir=None if args.output_dir is None else args.output_dir.resolve(),
        config=config,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
