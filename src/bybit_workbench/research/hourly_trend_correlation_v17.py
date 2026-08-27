from __future__ import annotations

import argparse
import csv
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from bybit_workbench.research.exit_break_even_v13 import (
    CoreSignal,
    SignalSource,
    _resolve_latest_uni_p40,
    _resolve_link_p40,
    discover_source,
    load_core_signals,
)
from bybit_workbench.research.flow_reversal_v1 import _archive_map, _load_trade_day

TrendLabel = Literal["bullish", "bearish", "mixed"]
RelationLabel = Literal["with", "against", "mixed"]
EmaPosition = Literal["above", "below", "equal"]
EmaSlope = Literal["rising", "falling", "flat"]

DEFAULT_POLICY_ID = "CORE050_RUN050_BE_MFE_GB4.00"


@dataclass(frozen=True, slots=True)
class HourCandle:
    start_at: datetime
    open: float
    high: float
    low: float
    close: float
    trade_count: int

    @property
    def end_at(self) -> datetime:
        return datetime.fromtimestamp(self.start_at.timestamp() + 3600.0, tz=UTC)


@dataclass(frozen=True, slots=True)
class EnrichedHour:
    candle: HourCandle
    ema20: float | None
    ema20_previous: float | None


@dataclass(frozen=True, slots=True)
class PolicyOutcome:
    symbol: str
    touch_at: datetime
    exit_reason: str
    exit_move_pct: float
    split_activated: bool
    core_component_pct: float
    runner_component_pct: float
    max_favorable_pct: float

    @property
    def runner_added(self) -> bool:
        return self.split_activated and self.runner_component_pct > 1e-12


@dataclass(frozen=True, slots=True)
class HourlyTrendFeature:
    symbol: str
    direction: str
    touch_at: datetime
    last_closed_hour_start: datetime
    hour_open: float
    hour_high: float
    hour_low: float
    hour_close: float
    hour_trade_count: int
    previous_hour_high: float
    previous_hour_low: float
    structure_1h: TrendLabel
    structure_relation: RelationLabel
    ema20: float
    ema20_position: EmaPosition
    ema20_relation: RelationLabel
    ema20_slope: EmaSlope
    combined_trend_1h: TrendLabel
    combined_relation: RelationLabel
    strict_trend_1h: TrendLabel
    strict_relation: RelationLabel
    exit_reason: str
    exit_move_pct: float
    split_activated: bool
    runner_added: bool
    core_component_pct: float
    runner_component_pct: float
    max_favorable_pct: float


@dataclass(frozen=True, slots=True)
class TrendCorrelationConfig:
    ema_period: int = 20
    selected_policy_id: str = DEFAULT_POLICY_ID
    progress_interval_seconds: float = 25.0

    def __post_init__(self) -> None:
        if self.ema_period < 2:
            raise ValueError("ema_period must be >= 2")
        if not self.selected_policy_id:
            raise ValueError("selected_policy_id is required")
        if self.progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")


class ProgressReporter:
    def __init__(self, interval_seconds: float = 25.0) -> None:
        self.interval_seconds = interval_seconds
        self.started = time.monotonic()
        self.last_emit = 0.0

    def emit(
        self,
        stage: str,
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
        eta = None
        if processed > 0 and total > processed:
            eta = elapsed / processed * (total - processed)
        eta_text = "n/a" if eta is None else _format_duration(eta)
        suffix = f" | {detail}" if detail else ""
        print(
            f"[P47D] stage={stage} processed={processed}/{total} "
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


def _parse_datetime(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "y"}


def _hour_start(timestamp: float) -> datetime:
    return datetime.fromtimestamp(math.floor(timestamp / 3600.0) * 3600.0, tz=UTC)


def aggregate_hourly_candles(
    archive_by_day: dict[str, Path],
    *,
    progress: ProgressReporter | None = None,
    symbol: str = "",
) -> tuple[HourCandle, ...]:
    candles: list[HourCandle] = []
    current_start: datetime | None = None
    open_price = 0.0
    high_price = 0.0
    low_price = 0.0
    close_price = 0.0
    trade_count = 0
    items = sorted(archive_by_day.items())

    def finish_current() -> None:
        nonlocal current_start, trade_count
        if current_start is None or trade_count <= 0:
            return
        candles.append(
            HourCandle(
                start_at=current_start,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                trade_count=trade_count,
            )
        )
        current_start = None
        trade_count = 0

    if progress is not None:
        progress.emit(
            "hourly-ohlc",
            processed=0,
            total=len(items),
            force=True,
            detail=f"symbol={symbol} raw public trades -> true 1H OHLC",
        )
    for index, (_, path) in enumerate(items, start=1):
        day = _load_trade_day(path)
        for timestamp, price in zip(day.timestamps, day.prices, strict=True):
            start = _hour_start(timestamp)
            if current_start != start:
                finish_current()
                current_start = start
                open_price = price
                high_price = price
                low_price = price
                close_price = price
                trade_count = 1
            else:
                high_price = max(high_price, price)
                low_price = min(low_price, price)
                close_price = price
                trade_count += 1
        if progress is not None:
            progress.emit(
                "hourly-ohlc",
                processed=index,
                total=len(items),
                detail=f"symbol={symbol} candles={len(candles)} archive={path.name}",
            )
    finish_current()
    return tuple(candles)


def enrich_ema(
    candles: tuple[HourCandle, ...],
    *,
    period: int,
) -> tuple[EnrichedHour, ...]:
    if period < 2:
        raise ValueError("period must be >= 2")
    alpha = 2.0 / (period + 1.0)
    ema: float | None = None
    previous_ema: float | None = None
    result: list[EnrichedHour] = []
    for index, candle in enumerate(candles):
        previous_ema = ema
        ema = candle.close if ema is None else alpha * candle.close + (1.0 - alpha) * ema
        result.append(
            EnrichedHour(
                candle=candle,
                ema20=ema if index >= period - 1 else None,
                ema20_previous=(
                    previous_ema if index >= period and previous_ema is not None else None
                ),
            )
        )
    return tuple(result)


def structure_label(current: HourCandle, previous: HourCandle) -> TrendLabel:
    if current.high > previous.high and current.low > previous.low:
        return "bullish"
    if current.high < previous.high and current.low < previous.low:
        return "bearish"
    return "mixed"


def ema_position(close: float, ema: float) -> EmaPosition:
    if close > ema:
        return "above"
    if close < ema:
        return "below"
    return "equal"


def ema_slope(current: float, previous: float) -> EmaSlope:
    if current > previous:
        return "rising"
    if current < previous:
        return "falling"
    return "flat"


def relation_to_direction(direction: str, trend: TrendLabel) -> RelationLabel:
    if trend == "mixed":
        return "mixed"
    expected = "bullish" if direction == "Long" else "bearish"
    return "with" if trend == expected else "against"


def ema_relation_to_direction(direction: str, position: EmaPosition) -> RelationLabel:
    if position == "equal":
        return "mixed"
    favorable = position == "above" if direction == "Long" else position == "below"
    return "with" if favorable else "against"


def combined_trend(structure: TrendLabel, position: EmaPosition) -> TrendLabel:
    if structure == "bullish" and position == "above":
        return "bullish"
    if structure == "bearish" and position == "below":
        return "bearish"
    return "mixed"


def strict_trend(
    structure: TrendLabel,
    position: EmaPosition,
    slope: EmaSlope,
) -> TrendLabel:
    if structure == "bullish" and position == "above" and slope == "rising":
        return "bullish"
    if structure == "bearish" and position == "below" and slope == "falling":
        return "bearish"
    return "mixed"


def _latest_closed_index(hours: tuple[EnrichedHour, ...], touch_at: datetime) -> int:
    touch_ts = touch_at.timestamp()
    candidate = -1
    for index, item in enumerate(hours):
        if item.candle.end_at.timestamp() <= touch_ts:
            candidate = index
        else:
            break
    return candidate


def build_feature(
    signal: CoreSignal,
    outcome: PolicyOutcome,
    hours: tuple[EnrichedHour, ...],
) -> HourlyTrendFeature:
    index = _latest_closed_index(hours, signal.touch_at)
    if index < 1:
        raise ValueError(f"not enough closed 1H candles before {signal.touch_at}")
    current = hours[index]
    previous = hours[index - 1]
    if current.ema20 is None or current.ema20_previous is None:
        raise ValueError(f"EMA20 warmup is incomplete before {signal.touch_at}")
    if (current.candle.start_at - previous.candle.start_at).total_seconds() != 3600.0:
        raise ValueError(f"1H archive gap before {signal.touch_at}")

    structure = structure_label(current.candle, previous.candle)
    position = ema_position(current.candle.close, current.ema20)
    slope = ema_slope(current.ema20, current.ema20_previous)
    combined = combined_trend(structure, position)
    strict = strict_trend(structure, position, slope)
    return HourlyTrendFeature(
        symbol=signal.symbol,
        direction=signal.direction,
        touch_at=signal.touch_at,
        last_closed_hour_start=current.candle.start_at,
        hour_open=current.candle.open,
        hour_high=current.candle.high,
        hour_low=current.candle.low,
        hour_close=current.candle.close,
        hour_trade_count=current.candle.trade_count,
        previous_hour_high=previous.candle.high,
        previous_hour_low=previous.candle.low,
        structure_1h=structure,
        structure_relation=relation_to_direction(signal.direction, structure),
        ema20=current.ema20,
        ema20_position=position,
        ema20_relation=ema_relation_to_direction(signal.direction, position),
        ema20_slope=slope,
        combined_trend_1h=combined,
        combined_relation=relation_to_direction(signal.direction, combined),
        strict_trend_1h=strict,
        strict_relation=relation_to_direction(signal.direction, strict),
        exit_reason=outcome.exit_reason,
        exit_move_pct=outcome.exit_move_pct,
        split_activated=outcome.split_activated,
        runner_added=outcome.runner_added,
        core_component_pct=outcome.core_component_pct,
        runner_component_pct=outcome.runner_component_pct,
        max_favorable_pct=outcome.max_favorable_pct,
    )


def _resolve_latest_p47c(root: Path) -> Path:
    base = root / "reports" / "core_runner_split_v1"
    candidates = sorted(
        (item for item in base.glob("UNI_LINK_*") if (item / "policy_results.csv").exists()),
        key=lambda item: item.stat().st_mtime,
    )
    if not candidates:
        raise FileNotFoundError(f"P47C report directory not found under {base}")
    return candidates[-1]


def load_policy_outcomes(
    report_dir: Path,
    *,
    policy_id: str,
) -> dict[tuple[str, datetime], PolicyOutcome]:
    path = report_dir / "policy_results.csv"
    if not path.exists():
        raise FileNotFoundError(f"P47C policy results not found: {path}")
    outcomes: dict[tuple[str, datetime], PolicyOutcome] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("policy_id") != policy_id:
                continue
            touch_at = _parse_datetime(row["touch_at"])
            outcome = PolicyOutcome(
                symbol=row["symbol"],
                touch_at=touch_at,
                exit_reason=row["exit_reason"],
                exit_move_pct=float(row["exit_move_pct"]),
                split_activated=_parse_bool(row["split_activated"]),
                core_component_pct=float(row["core_component_pct"]),
                runner_component_pct=float(row["runner_component_pct"]),
                max_favorable_pct=float(row["max_favorable_pct"]),
            )
            outcomes[(outcome.symbol, outcome.touch_at)] = outcome
    if not outcomes:
        raise ValueError(f"policy not found in P47C results: {policy_id}")
    return outcomes


def _feature_row(feature: HourlyTrendFeature) -> dict[str, Any]:
    row = asdict(feature)
    row["touch_at"] = feature.touch_at.isoformat()
    row["last_closed_hour_start"] = feature.last_closed_hour_start.isoformat()
    return row


def _scope_members(
    features: tuple[HourlyTrendFeature, ...],
    scope: str,
) -> list[HourlyTrendFeature]:
    if scope == "ALL_227":
        return list(features)
    if scope == "INITIAL_STOP_16":
        return [item for item in features if item.exit_reason == "initial_stop"]
    if scope == "SPLIT_SUCCESS_27":
        return [item for item in features if item.split_activated]
    if scope == "RUNNER_ADDED_7":
        return [item for item in features if item.runner_added]
    raise ValueError(f"unknown scope: {scope}")


def _counts(items: list[HourlyTrendFeature], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        value = str(getattr(item, field))
        result[value] = result.get(value, 0) + 1
    return result


def build_group_summary(features: tuple[HourlyTrendFeature, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope in ("ALL_227", "INITIAL_STOP_16", "SPLIT_SUCCESS_27", "RUNNER_ADDED_7"):
        scoped = _scope_members(features, scope)
        for symbol in ("POOLED_UNI_LINK", "UNIUSDT", "LINKUSDT"):
            items = (
                scoped
                if symbol == "POOLED_UNI_LINK"
                else [item for item in scoped if item.symbol == symbol]
            )
            structure = _counts(items, "structure_relation")
            ema = _counts(items, "ema20_relation")
            combined = _counts(items, "combined_relation")
            strict = _counts(items, "strict_relation")
            rows.append(
                {
                    "scope": scope,
                    "symbol": symbol,
                    "signals": len(items),
                    "structure_with": structure.get("with", 0),
                    "structure_against": structure.get("against", 0),
                    "structure_mixed": structure.get("mixed", 0),
                    "ema20_with": ema.get("with", 0),
                    "ema20_against": ema.get("against", 0),
                    "ema20_mixed": ema.get("mixed", 0),
                    "combined_with": combined.get("with", 0),
                    "combined_against": combined.get("against", 0),
                    "combined_mixed": combined.get("mixed", 0),
                    "strict_with": strict.get("with", 0),
                    "strict_against": strict.get("against", 0),
                    "strict_mixed": strict.get("mixed", 0),
                    "gross_selected_policy_pct": round(sum(x.exit_move_pct for x in items), 6),
                }
            )
    return rows


def build_bucket_performance(features: tuple[HourlyTrendFeature, ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for field in ("structure_relation", "ema20_relation", "combined_relation", "strict_relation"):
        for bucket in ("with", "against", "mixed"):
            items = [item for item in features if getattr(item, field) == bucket]
            if not items:
                continue
            success = sum(item.split_activated for item in items)
            runners = sum(item.runner_added for item in items)
            losses = sum(item.exit_reason == "initial_stop" for item in items)
            rows.append(
                {
                    "feature": field,
                    "relation": bucket,
                    "signals": len(items),
                    "split_success_count": success,
                    "split_success_rate_pct": round(success / len(items) * 100.0, 6),
                    "runner_added_count": runners,
                    "runner_added_rate_pct": round(runners / len(items) * 100.0, 6),
                    "initial_stop_count": losses,
                    "initial_stop_rate_pct": round(losses / len(items) * 100.0, 6),
                    "gross_selected_policy_pct": round(sum(x.exit_move_pct for x in items), 6),
                }
            )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_summary_md(
    path: Path,
    *,
    config: TrendCorrelationConfig,
    group_rows: list[dict[str, Any]],
    bucket_rows: list[dict[str, Any]],
    reference_check: dict[str, Any],
) -> None:
    pooled = {row["scope"]: row for row in group_rows if row["symbol"] == "POOLED_UNI_LINK"}
    lines = [
        "# P47D — Closed 1H Trend Correlation",
        "",
        "Entry and Exit are frozen. This is a diagnostic correlation only.",
        "",
        f"Selected P47C policy: `{config.selected_policy_id}`",
        "",
        "## Definitions",
        "",
        "- Only fully closed UTC 1H candles before `touch_at` are used.",
        "- OHLC is rebuilt from raw public trades, not from the current partial hour.",
        "- 1H structure bullish = higher high + higher low vs previous closed hour.",
        "- 1H structure bearish = lower high + lower low vs previous closed hour.",
        "- EMA20 relation uses the close of the last fully closed 1H candle.",
        "- Combined trend requires structure and EMA20 position to agree; otherwise mixed.",
        "- Strict trend additionally requires EMA20 slope to agree.",
        "",
        "## Reference check",
        "",
        f"`{json.dumps(reference_check, ensure_ascii=False)}`",
        "",
        "## Pooled relation counts",
        "",
        (
            "| scope | N | structure with/against/mixed | EMA20 with/against/mixed | "
            "combined with/against/mixed | strict with/against/mixed |"
        ),
        "|---|---:|---|---|---|---|",
    ]
    for scope in ("ALL_227", "INITIAL_STOP_16", "SPLIT_SUCCESS_27", "RUNNER_ADDED_7"):
        row = pooled[scope]
        lines.append(
            f"| {scope} | {row['signals']} | "
            f"{row['structure_with']}/{row['structure_against']}/{row['structure_mixed']} | "
            f"{row['ema20_with']}/{row['ema20_against']}/{row['ema20_mixed']} | "
            f"{row['combined_with']}/{row['combined_against']}/{row['combined_mixed']} | "
            f"{row['strict_with']}/{row['strict_against']}/{row['strict_mixed']} |"
        )
    lines.extend(
        [
            "",
            "## Conditional rates across all 227 signals",
            "",
            (
                "| feature | relation | N | success +1.10 % | runner-added % | "
                "initial-stop % | gross selected policy % |"
            ),
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in bucket_rows:
        lines.append(
            f"| {row['feature']} | {row['relation']} | {row['signals']} | "
            f"{row['split_success_rate_pct']} | {row['runner_added_rate_pct']} | "
            f"{row['initial_stop_rate_pct']} | {row['gross_selected_policy_pct']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This report is correlation, not a new Entry filter.",
            "- Do not retune Entry V1 from this report.",
            "- The 7 runner-added cases are a small sample; treat them as hypothesis-generating.",
            "- Out-of-sample validation must precede any use of 1H context as a live rule.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_research(
    sources: tuple[SignalSource, ...],
    *,
    p47c_dir: Path,
    output_dir: Path,
    config: TrendCorrelationConfig,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    outcomes = load_policy_outcomes(p47c_dir, policy_id=config.selected_policy_id)
    features: list[HourlyTrendFeature] = []
    total_signal_count = 0
    progress = ProgressReporter(config.progress_interval_seconds)

    for source in sources:
        signals = tuple(sorted(load_core_signals(source), key=lambda item: item.touch_at))
        total_signal_count += len(signals)
        archive_by_day = _archive_map(source.dataset_dir)
        candles = aggregate_hourly_candles(
            archive_by_day,
            progress=progress,
            symbol=source.symbol,
        )
        hours = enrich_ema(candles, period=config.ema_period)
        for signal in signals:
            outcome = outcomes.get((signal.symbol, signal.touch_at))
            if outcome is None:
                raise ValueError(
                    f"P47C outcome missing for {signal.symbol} {signal.touch_at.isoformat()}"
                )
            features.append(build_feature(signal, outcome, hours))

    feature_tuple = tuple(sorted(features, key=lambda item: (item.touch_at, item.symbol)))
    reference_check = {
        "signals_expected": 227,
        "signals_actual": total_signal_count,
        "initial_stop_expected": 16,
        "initial_stop_actual": sum(item.exit_reason == "initial_stop" for item in feature_tuple),
        "split_success_expected": 27,
        "split_success_actual": sum(item.split_activated for item in feature_tuple),
        "runner_added_expected": 7,
        "runner_added_actual": sum(item.runner_added for item in feature_tuple),
    }
    reference_check["all_match"] = all(
        reference_check[key.replace("_actual", "_expected")] == value
        for key, value in reference_check.items()
        if key.endswith("_actual")
    )

    group_rows = build_group_summary(feature_tuple)
    bucket_rows = build_bucket_performance(feature_tuple)
    _write_csv(output_dir / "hourly_features.csv", [_feature_row(item) for item in feature_tuple])
    _write_csv(output_dir / "group_summary.csv", group_rows)
    _write_csv(output_dir / "trend_bucket_performance.csv", bucket_rows)

    summary = {
        "architecture": "p47d_closed_1h_trend_correlation_v1",
        "research_only": True,
        "entry_frozen": True,
        "exit_frozen": True,
        "p47c_dir": str(p47c_dir),
        "config": asdict(config),
        "reference_check": reference_check,
        "signals": len(feature_tuple),
        "notes": [
            "True 1H OHLC is rebuilt from raw public trades.",
            "Only fully closed hours before touch_at are used; partial current hour is excluded.",
            "Combined trend = HH/HL or LH/LL structure aligned with close vs EMA20.",
            "Strict trend also requires EMA20 slope alignment.",
            "This is correlation only; no Entry/Exit parameter is changed.",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    _write_summary_md(
        output_dir / "summary.md",
        config=config,
        group_rows=group_rows,
        bucket_rows=bucket_rows,
        reference_check=reference_check,
    )
    progress.emit(
        "done",
        processed=1,
        total=1,
        force=True,
        detail=f"output={output_dir} reference_check={reference_check['all_match']}",
    )
    return summary


def _default_output_dir(root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return root / "reports" / "hourly_trend_correlation_v1" / f"UNI_LINK_{stamp}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P47D: correlate frozen P47C outcomes with true closed 1H structure + EMA20"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--uni-p40-dir", type=Path)
    parser.add_argument("--link-p40-dir", type=Path)
    parser.add_argument("--p47c-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--ema-period", type=int, default=20)
    parser.add_argument("--selected-policy-id", default=DEFAULT_POLICY_ID)
    parser.add_argument("--progress-interval-seconds", type=float, default=25.0)
    args = parser.parse_args()

    root = args.root.resolve()
    uni_dir = args.uni_p40_dir or _resolve_latest_uni_p40(root)
    link_dir = args.link_p40_dir or _resolve_link_p40(root)
    p47c_dir = (args.p47c_dir or _resolve_latest_p47c(root)).resolve()
    output_dir = (args.output_dir or _default_output_dir(root)).resolve()
    config = TrendCorrelationConfig(
        ema_period=args.ema_period,
        selected_policy_id=args.selected_policy_id,
        progress_interval_seconds=args.progress_interval_seconds,
    )
    sources = (discover_source(uni_dir), discover_source(link_dir))
    summary = run_research(
        sources,
        p47c_dir=p47c_dir,
        output_dir=output_dir,
        config=config,
    )
    print(f"P47D signals: {summary['signals']}")
    print(f"Reference check: {summary['reference_check']['all_match']}")
    print(f"Report: {output_dir / 'summary.json'}")
    print(f"Readable summary: {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
