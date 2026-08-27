from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.mtf_entry import _decimal_json

Direction = Literal["Long", "Short"]
Outcome = Literal["favorable_first", "adverse_first", "neither"]
OiQuartile = Literal["Q1", "Q2", "Q3", "Q4"]


@dataclass(frozen=True, slots=True)
class OiResearchConfig:
    symbol: str = "UNIUSDT"
    windows_minutes: tuple[int, ...] = (5, 15, 30, 60)
    failure_embargo_minutes: int = 60
    price_invalidation_percent: Decimal = Decimal("1.0")

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if not self.windows_minutes or any(value <= 0 for value in self.windows_minutes):
            raise ValueError("OI windows must be positive")
        if self.failure_embargo_minutes <= 0:
            raise ValueError("failure embargo must be positive")
        if self.price_invalidation_percent <= 0:
            raise ValueError("price invalidation percent must be positive")


@dataclass(frozen=True, slots=True)
class P33Signal:
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    entry_price: Decimal
    touch_at: datetime
    hourly_alignment: str
    flow_state: str
    exact_mae_30m_pct: Decimal | None
    first_0_5_vs_1_0: Outcome
    first_1_0_vs_1_0: Outcome
    seconds_to_minus_1_0: float | None
    seconds_to_plus_0_5: float | None
    seconds_to_plus_1_0: float | None


@dataclass(frozen=True, slots=True)
class OiPoint:
    timestamp: datetime
    open_interest: Decimal


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: datetime
    close: Decimal


@dataclass(frozen=True, slots=True)
class AnnotatedSignal:
    source: P33Signal
    accepted_after_failure_embargo: bool
    oi_anchor_at: datetime | None
    oi_change_5m_pct: Decimal | None
    oi_change_15m_pct: Decimal | None
    oi_change_30m_pct: Decimal | None
    oi_change_60m_pct: Decimal | None
    oi_acceleration_5_vs_60: Decimal | None
    directed_price_return_60m_pct: Decimal | None
    oi_state: str
    oi_price_regime_60m: str
    oi_60m_quartile: OiQuartile | None = None
    oi_accel_quartile: OiQuartile | None = None


@dataclass(frozen=True, slots=True)
class SeriesIndex:
    timestamps: tuple[datetime, ...]
    values: tuple[Decimal, ...]

    def value_at_or_before(self, timestamp: datetime) -> tuple[datetime, Decimal] | None:
        index = bisect.bisect_right(self.timestamps, timestamp) - 1
        if index < 0:
            return None
        return self.timestamps[index], self.values[index]


def _parse_decimal(value: str) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def _parse_float(value: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    return float(text)


def _parse_outcome(value: str) -> Outcome:
    if value not in {"favorable_first", "adverse_first", "neither"}:
        raise ValueError(f"unsupported outcome: {value!r}")
    return cast(Outcome, value)


def _parse_direction(value: str) -> Direction:
    if value not in {"Long", "Short"}:
        raise ValueError(f"unsupported direction: {value!r}")
    return cast(Direction, value)


def _read_p33_signals(path: Path, *, symbol: str) -> tuple[P33Signal, ...]:
    rows: list[P33Signal] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("symbol") != symbol:
                continue
            rows.append(
                P33Signal(
                    symbol=symbol,
                    direction=_parse_direction(raw["direction"]),
                    candidate_bar_at=datetime.fromisoformat(raw["candidate_bar_at"]).astimezone(
                        UTC
                    ),
                    entry_price=Decimal(raw["entry_price"]),
                    touch_at=datetime.fromisoformat(raw["touch_at"]).astimezone(UTC),
                    hourly_alignment=raw["hourly_alignment"],
                    flow_state=raw["flow_state"],
                    exact_mae_30m_pct=_parse_decimal(raw["exact_mae_30m_pct"]),
                    first_0_5_vs_1_0=_parse_outcome(raw["first_0_5_vs_1_0"]),
                    first_1_0_vs_1_0=_parse_outcome(raw["first_1_0_vs_1_0"]),
                    seconds_to_minus_1_0=_parse_float(raw["seconds_to_minus_1_0"]),
                    seconds_to_plus_0_5=_parse_float(raw["seconds_to_plus_0_5"]),
                    seconds_to_plus_1_0=_parse_float(raw["seconds_to_plus_1_0"]),
                )
            )
    return tuple(rows)


def _read_oi(path: Path) -> tuple[OiPoint, ...]:
    rows: list[OiPoint] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                OiPoint(
                    timestamp=datetime.fromisoformat(raw["timestamp"]).astimezone(UTC),
                    open_interest=Decimal(raw["open_interest"]),
                )
            )
    rows.sort(key=lambda item: item.timestamp)
    return tuple(rows)


def _read_prices(path: Path, *, symbol: str) -> tuple[PricePoint, ...]:
    rows: list[PricePoint] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                PricePoint(
                    timestamp=datetime.fromisoformat(raw["closed_at"]).astimezone(UTC),
                    close=Decimal(raw["close"]),
                )
            )
    rows.sort(key=lambda item: item.timestamp)
    if not rows:
        raise ValueError(f"no {symbol} 5m prices found in {path}")
    return tuple(rows)


def _series_from_oi(rows: tuple[OiPoint, ...]) -> SeriesIndex:
    return SeriesIndex(
        timestamps=tuple(item.timestamp for item in rows),
        values=tuple(item.open_interest for item in rows),
    )


def _series_from_prices(rows: tuple[PricePoint, ...]) -> SeriesIndex:
    return SeriesIndex(
        timestamps=tuple(item.timestamp for item in rows),
        values=tuple(item.close for item in rows),
    )


def _percent_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous <= 0:
        return None
    return (current / previous - Decimal("1")) * Decimal("100")


def _oi_change(
    series: SeriesIndex,
    anchor_at: datetime,
    window_minutes: int,
) -> tuple[datetime, Decimal] | None:
    current = series.value_at_or_before(anchor_at)
    previous = series.value_at_or_before(anchor_at - timedelta(minutes=window_minutes))
    if current is None or previous is None:
        return None
    change = _percent_change(current[1], previous[1])
    if change is None:
        return None
    return current[0], change


def _directed_price_return_60m(
    signal: P33Signal,
    prices: SeriesIndex,
) -> Decimal | None:
    previous = prices.value_at_or_before(signal.touch_at - timedelta(minutes=60))
    if previous is None or previous[1] <= 0:
        return None
    raw = (signal.entry_price / previous[1] - Decimal("1")) * Decimal("100")
    return raw if signal.direction == "Long" else -raw


def _oi_state(
    change_5m: Decimal | None,
    change_15m: Decimal | None,
    change_60m: Decimal | None,
) -> str:
    if change_5m is None or change_15m is None or change_60m is None:
        return "missing"
    if change_60m > 0:
        if change_15m > 0 and change_5m > 0:
            return "expansion_continues"
        if change_15m > 0 and change_5m <= 0:
            return "expansion_stalls"
        if change_15m <= 0:
            return "expansion_reverses"
    if change_60m < 0:
        if change_15m < 0 and change_5m < 0:
            return "deleveraging_continues"
        if change_15m < 0 and change_5m >= 0:
            return "deleveraging_stalls"
        if change_15m >= 0:
            return "deleveraging_reverses"
    return "flat_or_mixed"


def _oi_price_regime(
    directed_price_return: Decimal | None,
    oi_change_60m: Decimal | None,
) -> str:
    if directed_price_return is None or oi_change_60m is None:
        return "missing"
    price_side = "favorable_price" if directed_price_return >= 0 else "adverse_price"
    oi_side = "oi_expanding" if oi_change_60m >= 0 else "oi_contracting"
    return f"{price_side}_{oi_side}"


def _embargo_acceptance(
    signals: tuple[P33Signal, ...],
    *,
    minutes: int,
) -> dict[datetime, bool]:
    accepted: dict[datetime, bool] = {}
    embargo_until: datetime | None = None
    for signal in sorted(signals, key=lambda item: item.touch_at):
        if embargo_until is not None and signal.touch_at < embargo_until:
            accepted[signal.touch_at] = False
            continue
        accepted[signal.touch_at] = True
        if signal.first_0_5_vs_1_0 != "adverse_first":
            continue
        if signal.seconds_to_minus_1_0 is None:
            continue
        failure_at = signal.touch_at + timedelta(seconds=signal.seconds_to_minus_1_0)
        embargo_until = failure_at + timedelta(minutes=minutes)
    return accepted


def _annotate(
    signals: tuple[P33Signal, ...],
    oi_series: SeriesIndex,
    price_series: SeriesIndex,
    *,
    config: OiResearchConfig,
) -> tuple[AnnotatedSignal, ...]:
    acceptance = _embargo_acceptance(signals, minutes=config.failure_embargo_minutes)
    rows: list[AnnotatedSignal] = []
    for signal in signals:
        changes: dict[int, Decimal | None] = {}
        anchor_at: datetime | None = None
        for minutes in config.windows_minutes:
            result = _oi_change(oi_series, signal.candidate_bar_at, minutes)
            if result is None:
                changes[minutes] = None
                continue
            anchor_at = result[0]
            changes[minutes] = result[1]
        change_5m = changes.get(5)
        change_15m = changes.get(15)
        change_30m = changes.get(30)
        change_60m = changes.get(60)
        acceleration: Decimal | None = None
        if change_5m is not None and change_60m is not None:
            acceleration = change_5m - change_60m / Decimal("12")
        directed_price = _directed_price_return_60m(signal, price_series)
        rows.append(
            AnnotatedSignal(
                source=signal,
                accepted_after_failure_embargo=acceptance.get(signal.touch_at, True),
                oi_anchor_at=anchor_at,
                oi_change_5m_pct=change_5m,
                oi_change_15m_pct=change_15m,
                oi_change_30m_pct=change_30m,
                oi_change_60m_pct=change_60m,
                oi_acceleration_5_vs_60=acceleration,
                directed_price_return_60m_pct=directed_price,
                oi_state=_oi_state(change_5m, change_15m, change_60m),
                oi_price_regime_60m=_oi_price_regime(directed_price, change_60m),
            )
        )
    return _assign_quartiles(tuple(rows))


def _quartile_bounds(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    if len(values) < 4:
        raise ValueError("at least four values are required for quartiles")
    float_values = [float(value) for value in values]
    q1, q2, q3 = statistics.quantiles(float_values, n=4, method="inclusive")
    return Decimal(str(q1)), Decimal(str(q2)), Decimal(str(q3))


def _quartile(value: Decimal | None, bounds: tuple[Decimal, Decimal, Decimal]) -> OiQuartile | None:
    if value is None:
        return None
    q1, q2, q3 = bounds
    if value <= q1:
        return "Q1"
    if value <= q2:
        return "Q2"
    if value <= q3:
        return "Q3"
    return "Q4"


def _assign_quartiles(signals: tuple[AnnotatedSignal, ...]) -> tuple[AnnotatedSignal, ...]:
    oi_values = [item.oi_change_60m_pct for item in signals if item.oi_change_60m_pct is not None]
    accel_values = [
        item.oi_acceleration_5_vs_60
        for item in signals
        if item.oi_acceleration_5_vs_60 is not None
    ]
    if len(oi_values) < 4 or len(accel_values) < 4:
        return signals
    oi_bounds = _quartile_bounds(oi_values)
    accel_bounds = _quartile_bounds(accel_values)
    rows: list[AnnotatedSignal] = []
    for item in signals:
        rows.append(
            AnnotatedSignal(
                source=item.source,
                accepted_after_failure_embargo=item.accepted_after_failure_embargo,
                oi_anchor_at=item.oi_anchor_at,
                oi_change_5m_pct=item.oi_change_5m_pct,
                oi_change_15m_pct=item.oi_change_15m_pct,
                oi_change_30m_pct=item.oi_change_30m_pct,
                oi_change_60m_pct=item.oi_change_60m_pct,
                oi_acceleration_5_vs_60=item.oi_acceleration_5_vs_60,
                directed_price_return_60m_pct=item.directed_price_return_60m_pct,
                oi_state=item.oi_state,
                oi_price_regime_60m=item.oi_price_regime_60m,
                oi_60m_quartile=_quartile(item.oi_change_60m_pct, oi_bounds),
                oi_accel_quartile=_quartile(item.oi_acceleration_5_vs_60, accel_bounds),
            )
        )
    return tuple(rows)


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100.0 / denominator, 2)


def _metrics(signals: list[AnnotatedSignal]) -> dict[str, Any]:
    total = len(signals)
    favorable_half = sum(
        item.source.first_0_5_vs_1_0 == "favorable_first" for item in signals
    )
    adverse_half = sum(item.source.first_0_5_vs_1_0 == "adverse_first" for item in signals)
    favorable_one = sum(
        item.source.first_1_0_vs_1_0 == "favorable_first" for item in signals
    )
    hit_half = sum(item.source.seconds_to_plus_0_5 is not None for item in signals)
    hit_one = sum(item.source.seconds_to_plus_1_0 is not None for item in signals)
    minus_one_30m = sum(
        item.source.exact_mae_30m_pct is not None
        and item.source.exact_mae_30m_pct <= Decimal("-1")
        for item in signals
    )
    return {
        "signals": total,
        "plus_0_5_before_minus_1_percent": _percent(favorable_half, total),
        "minus_1_before_plus_0_5_percent": _percent(adverse_half, total),
        "plus_1_before_minus_1_percent": _percent(favorable_one, total),
        "eventual_plus_0_5_hit_percent": _percent(hit_half, total),
        "eventual_plus_1_hit_percent": _percent(hit_one, total),
        "minus_1_within_30m_percent": _percent(minus_one_30m, total),
    }


def _group_rows(
    signals: tuple[AnnotatedSignal, ...],
    *,
    feature: str,
    value_getter: Any,
    minimum_signals: int = 1,
) -> list[dict[str, Any]]:
    groups: dict[str, list[AnnotatedSignal]] = {}
    for item in signals:
        value = value_getter(item)
        if value is None:
            continue
        groups.setdefault(str(value), []).append(item)
    rows: list[dict[str, Any]] = []
    for value, group in sorted(groups.items()):
        if len(group) < minimum_signals:
            continue
        rows.append({"feature": feature, "value": value, **_metrics(group)})
    return rows


def _monthly_rows(
    signals: tuple[AnnotatedSignal, ...],
    *,
    evaluation_start: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for month_index in range(3):
        start = evaluation_start + timedelta(days=30 * month_index)
        end = start + timedelta(days=30)
        month_signals = [item for item in signals if start <= item.source.touch_at < end]
        rows.append({"segment": month_index + 1, "scope": "all", **_metrics(month_signals)})
        accepted = [item for item in month_signals if item.accepted_after_failure_embargo]
        rows.append(
            {
                "segment": month_index + 1,
                "scope": "accepted_after_60m_invalidation_pause",
                **_metrics(accepted),
            }
        )
        for quartile in ("Q1", "Q2", "Q3", "Q4"):
            group = [item for item in month_signals if item.oi_60m_quartile == quartile]
            rows.append(
                {
                    "segment": month_index + 1,
                    "scope": f"oi_60m_{quartile}",
                    **_metrics(group),
                }
            )
            accepted_group = [
                item
                for item in group
                if item.accepted_after_failure_embargo
            ]
            rows.append(
                {
                    "segment": month_index + 1,
                    "scope": f"accepted_oi_60m_{quartile}",
                    **_metrics(accepted_group),
                }
            )
            accel_group = [
                item for item in month_signals if item.oi_accel_quartile == quartile
            ]
            rows.append(
                {
                    "segment": month_index + 1,
                    "scope": f"oi_accel_{quartile}",
                    **_metrics(accel_group),
                }
            )
            accepted_accel = [
                item
                for item in accel_group
                if item.accepted_after_failure_embargo
            ]
            rows.append(
                {
                    "segment": month_index + 1,
                    "scope": f"accepted_oi_accel_{quartile}",
                    **_metrics(accepted_accel),
                }
            )
    return rows


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return _decimal_json(value)


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_signals(path: Path, signals: tuple[AnnotatedSignal, ...]) -> None:
    fields = [
        "symbol",
        "direction",
        "candidate_bar_at",
        "touch_at",
        "entry_price",
        "hourly_alignment",
        "flow_state",
        "first_0_5_vs_1_0",
        "first_1_0_vs_1_0",
        "accepted_after_failure_embargo",
        "oi_anchor_at",
        "oi_change_5m_pct",
        "oi_change_15m_pct",
        "oi_change_30m_pct",
        "oi_change_60m_pct",
        "oi_acceleration_5_vs_60",
        "oi_60m_quartile",
        "oi_accel_quartile",
        "directed_price_return_60m_pct",
        "oi_state",
        "oi_price_regime_60m",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in signals:
            writer.writerow(
                {
                    "symbol": item.source.symbol,
                    "direction": item.source.direction,
                    "candidate_bar_at": item.source.candidate_bar_at.isoformat(),
                    "touch_at": item.source.touch_at.isoformat(),
                    "entry_price": item.source.entry_price,
                    "hourly_alignment": item.source.hourly_alignment,
                    "flow_state": item.source.flow_state,
                    "first_0_5_vs_1_0": item.source.first_0_5_vs_1_0,
                    "first_1_0_vs_1_0": item.source.first_1_0_vs_1_0,
                    "accepted_after_failure_embargo": item.accepted_after_failure_embargo,
                    "oi_anchor_at": (
                        None if item.oi_anchor_at is None else item.oi_anchor_at.isoformat()
                    ),
                    "oi_change_5m_pct": item.oi_change_5m_pct,
                    "oi_change_15m_pct": item.oi_change_15m_pct,
                    "oi_change_30m_pct": item.oi_change_30m_pct,
                    "oi_change_60m_pct": item.oi_change_60m_pct,
                    "oi_acceleration_5_vs_60": item.oi_acceleration_5_vs_60,
                    "oi_60m_quartile": item.oi_60m_quartile,
                    "oi_accel_quartile": item.oi_accel_quartile,
                    "directed_price_return_60m_pct": item.directed_price_return_60m_pct,
                    "oi_state": item.oi_state,
                    "oi_price_regime_60m": item.oi_price_regime_60m,
                }
            )


def _p33_metadata(p33_dir: Path) -> tuple[Path, datetime, datetime]:
    summary_path = p33_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"P33 summary not found: {summary_path}")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    dataset_dir = Path(str(payload["dataset_dir"]))
    evaluation_start = datetime.fromisoformat(str(payload["evaluation_start"])).astimezone(UTC)
    evaluation_end = datetime.fromisoformat(str(payload["evaluation_end"])).astimezone(UTC)
    return dataset_dir, evaluation_start, evaluation_end


def run_open_interest_research(
    p33_dir: Path,
    *,
    config: OiResearchConfig,
    dataset_dir_override: Path | None = None,
) -> tuple[tuple[AnnotatedSignal, ...], Path, datetime, datetime]:
    dataset_dir, evaluation_start, evaluation_end = _p33_metadata(p33_dir)
    if dataset_dir_override is not None:
        dataset_dir = dataset_dir_override
    signals = _read_p33_signals(p33_dir / "signals_adverse_path.csv", symbol=config.symbol)
    oi_rows = _read_oi(dataset_dir / "open_interest_5m.csv")
    price_rows = _read_prices(dataset_dir / "trade_5m.csv", symbol=config.symbol)
    annotated = _annotate(
        signals,
        _series_from_oi(oi_rows),
        _series_from_prices(price_rows),
        config=config,
    )
    return annotated, dataset_dir, evaluation_start, evaluation_end


def _build_summary(
    signals: tuple[AnnotatedSignal, ...],
    *,
    config: OiResearchConfig,
    dataset_dir: Path,
    p33_dir: Path,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> dict[str, Any]:
    accepted = [item for item in signals if item.accepted_after_failure_embargo]
    quartile_rows = _group_rows(
        signals,
        feature="oi_change_60m_quartile",
        value_getter=lambda item: item.oi_60m_quartile,
    )
    accel_rows = _group_rows(
        signals,
        feature="oi_acceleration_quartile",
        value_getter=lambda item: item.oi_accel_quartile,
    )
    state_rows = _group_rows(
        signals,
        feature="oi_state",
        value_getter=lambda item: item.oi_state,
    )
    regime_rows = _group_rows(
        signals,
        feature="oi_price_regime_60m",
        value_getter=lambda item: item.oi_price_regime_60m,
    )
    accepted_quartile_rows = _group_rows(
        tuple(accepted),
        feature="oi_change_60m_quartile",
        value_getter=lambda item: item.oi_60m_quartile,
    )
    accepted_accel_rows = _group_rows(
        tuple(accepted),
        feature="oi_acceleration_quartile",
        value_getter=lambda item: item.oi_accel_quartile,
    )
    return {
        "architecture": "p34_open_interest_entry_context",
        "dataset_dir": dataset_dir,
        "p33_dir": p33_dir,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "config": asdict(config),
        "risk_convention": {
            "price_invalidation_percent": config.price_invalidation_percent,
            "meaning": "percent move from entry price, not percent of account equity",
            "live_risk_engine_changed": False,
        },
        "baseline": _metrics(list(signals)),
        "after_60m_invalidation_pause": _metrics(accepted),
        "oi_60m_quartiles": quartile_rows,
        "oi_acceleration_quartiles": accel_rows,
        "after_60m_pause_oi_60m_quartiles": accepted_quartile_rows,
        "after_60m_pause_oi_acceleration_quartiles": accepted_accel_rows,
        "oi_states": state_rows,
        "oi_price_regimes_60m": regime_rows,
        "notes": [
            "P34 changes no live trading, stop-loss, take-profit, or exit logic.",
            "The strategy research convention now records 1% as price invalidation from entry.",
            "Open interest is diagnostic only; no OI threshold is promoted into an entry gate.",
            "OI snapshots are anchored at or before the candidate 5m bar start to avoid lookahead.",
            (
                "The 60m post-invalidation pause is carried only as a causal "
                "candidate-filter baseline."
            ),
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P34 open-interest entry-context research")
    parser.add_argument("--symbol", default="UNIUSDT")
    parser.add_argument("--p33-dir", required=True)
    parser.add_argument("--dataset-dir", default="")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args(argv)

    config = OiResearchConfig(symbol=args.symbol)
    p33_dir = Path(args.p33_dir)
    dataset_override = Path(args.dataset_dir) if args.dataset_dir else None
    signals, dataset_dir, evaluation_start, evaluation_end = run_open_interest_research(
        p33_dir,
        config=config,
        dataset_dir_override=dataset_override,
    )

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = Path("reports") / "entry_research_v7" / f"{config.symbol}_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    _write_signals(output_dir / "signals_open_interest.csv", signals)
    quartile_rows = _group_rows(
        signals,
        feature="oi_change_60m_quartile",
        value_getter=lambda item: item.oi_60m_quartile,
    ) + _group_rows(
        signals,
        feature="oi_acceleration_quartile",
        value_getter=lambda item: item.oi_accel_quartile,
    )
    _write_rows(output_dir / "oi_quartiles.csv", quartile_rows)
    _write_rows(
        output_dir / "oi_states.csv",
        _group_rows(signals, feature="oi_state", value_getter=lambda item: item.oi_state),
    )
    _write_rows(
        output_dir / "oi_price_regimes.csv",
        _group_rows(
            signals,
            feature="oi_price_regime_60m",
            value_getter=lambda item: item.oi_price_regime_60m,
        ),
    )

    interaction_rows: list[dict[str, Any]] = []
    flow_states = sorted({item.source.flow_state for item in signals})
    quartiles: tuple[OiQuartile, ...] = ("Q1", "Q2", "Q3", "Q4")
    for flow_state in flow_states:
        for quartile in quartiles:
            group = [
                item
                for item in signals
                if item.source.flow_state == flow_state and item.oi_60m_quartile == quartile
            ]
            if len(group) < 20:
                continue
            interaction_rows.append(
                {
                    "flow_state": flow_state,
                    "oi_60m_quartile": quartile,
                    **_metrics(group),
                }
            )
    _write_rows(output_dir / "flow_oi_interactions.csv", interaction_rows)
    _write_rows(
        output_dir / "monthly_stability.csv",
        _monthly_rows(signals, evaluation_start=evaluation_start),
    )

    summary = _build_summary(
        signals,
        config=config,
        dataset_dir=dataset_dir,
        p33_dir=p33_dir,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )
    _write_json(output_dir / "summary.json", summary)

    print(f"Dataset: {dataset_dir}")
    print(f"P33 source: {p33_dir}")
    print(f"Signals analysed: {len(signals)}")
    print(
        "P34 60m invalidation-pause accepted: "
        f"{sum(item.accepted_after_failure_embargo for item in signals)}"
    )
    print(f"Report: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
