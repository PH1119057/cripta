from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
import time
import urllib.parse
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, cast

from bybit_workbench.research.mtf_entry import _decimal_json
from bybit_workbench.research.mtf_entry_v3 import _http_request

Direction = Literal["Long", "Short"]
Outcome = Literal["favorable_first", "adverse_first", "neither"]
Quartile = Literal["Q1", "Q2", "Q3", "Q4"]


@dataclass(frozen=True, slots=True)
class BasisResearchConfig:
    symbol: str = "UNIUSDT"
    endpoint: str = "https://api.bybit.kz"
    interval: str = "5"
    windows_minutes: tuple[int, ...] = (5, 15, 30, 60)
    request_pause_seconds: float = 0.15
    minimum_interaction_signals: int = 20

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if not self.endpoint.startswith("https://"):
            raise ValueError("endpoint must use https")
        if self.interval != "5":
            raise ValueError("P36 currently requires 5m mark/index data")
        if not self.windows_minutes or any(value <= 0 for value in self.windows_minutes):
            raise ValueError("basis windows must be positive")
        if self.request_pause_seconds < 0:
            raise ValueError("request pause cannot be negative")
        if self.minimum_interaction_signals <= 0:
            raise ValueError("minimum interaction size must be positive")


@dataclass(frozen=True, slots=True)
class P35Signal:
    symbol: str
    direction: Direction
    candidate_bar_at: datetime
    touch_at: datetime
    entry_price: Decimal
    hourly_alignment: str
    flow_state: str
    first_0_5_vs_1_0: Outcome
    first_1_0_vs_1_0: Outcome
    accepted_after_failure_embargo: bool
    oi_change_60m_pct: Decimal | None
    oi_acceleration_5_vs_60: Decimal | None
    crowd_acceleration_5_vs_60: Decimal | None


@dataclass(frozen=True, slots=True)
class PricePoint:
    timestamp: datetime
    close_price: Decimal


@dataclass(frozen=True, slots=True)
class BasisPoint:
    timestamp: datetime
    mark_price: Decimal
    index_price: Decimal
    basis_bps: Decimal


@dataclass(frozen=True, slots=True)
class BasisSeriesIndex:
    timestamps: tuple[datetime, ...]
    points: tuple[BasisPoint, ...]

    def point_strictly_before(self, timestamp: datetime) -> BasisPoint | None:
        index = bisect.bisect_left(self.timestamps, timestamp) - 1
        if index < 0:
            return None
        return self.points[index]

    def point_at_or_before(self, timestamp: datetime) -> BasisPoint | None:
        index = bisect.bisect_right(self.timestamps, timestamp) - 1
        if index < 0:
            return None
        return self.points[index]


@dataclass(frozen=True, slots=True)
class AnnotatedBasisSignal:
    source: P35Signal
    basis_anchor_at: datetime | None
    mark_price: Decimal | None
    index_price: Decimal | None
    basis_bps: Decimal | None
    directional_basis_bps: Decimal | None
    directional_change_5m_bps: Decimal | None
    directional_change_15m_bps: Decimal | None
    directional_change_30m_bps: Decimal | None
    directional_change_60m_bps: Decimal | None
    basis_acceleration_5_vs_60_bps: Decimal | None
    basis_state: str
    oi_tail_danger: bool
    basis_level_quartile: Quartile | None = None
    basis_change_60m_quartile: Quartile | None = None
    basis_accel_quartile: Quartile | None = None


def _parse_decimal(value: str) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes"}


def _parse_direction(value: str) -> Direction:
    if value not in {"Long", "Short"}:
        raise ValueError(f"unsupported direction: {value!r}")
    return cast(Direction, value)


def _parse_outcome(value: str) -> Outcome:
    if value not in {"favorable_first", "adverse_first", "neither"}:
        raise ValueError(f"unsupported outcome: {value!r}")
    return cast(Outcome, value)


def _read_p35_signals(path: Path, *, symbol: str) -> tuple[P35Signal, ...]:
    rows: list[P35Signal] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("symbol") != symbol:
                continue
            rows.append(
                P35Signal(
                    symbol=symbol,
                    direction=_parse_direction(raw["direction"]),
                    candidate_bar_at=datetime.fromisoformat(
                        raw["candidate_bar_at"]
                    ).astimezone(UTC),
                    touch_at=datetime.fromisoformat(raw["touch_at"]).astimezone(UTC),
                    entry_price=Decimal(raw["entry_price"]),
                    hourly_alignment=raw["hourly_alignment"],
                    flow_state=raw["flow_state"],
                    first_0_5_vs_1_0=_parse_outcome(raw["first_0_5_vs_1_0"]),
                    first_1_0_vs_1_0=_parse_outcome(raw["first_1_0_vs_1_0"]),
                    accepted_after_failure_embargo=_parse_bool(
                        raw["accepted_after_failure_embargo"]
                    ),
                    oi_change_60m_pct=_parse_decimal(raw["oi_change_60m_pct"]),
                    oi_acceleration_5_vs_60=_parse_decimal(
                        raw["oi_acceleration_5_vs_60"]
                    ),
                    crowd_acceleration_5_vs_60=_parse_decimal(
                        raw["crowd_acceleration_5_vs_60"]
                    ),
                )
            )
    return tuple(rows)


def _read_price_points(path: Path) -> tuple[PricePoint, ...]:
    rows: list[PricePoint] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                PricePoint(
                    timestamp=datetime.fromisoformat(raw["timestamp"]).astimezone(UTC),
                    close_price=Decimal(raw["close_price"]),
                )
            )
    rows.sort(key=lambda item: item.timestamp)
    return tuple(rows)


def _write_price_points(path: Path, rows: tuple[PricePoint, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "close_price"])
        writer.writeheader()
        for item in rows:
            writer.writerow(
                {
                    "timestamp": item.timestamp.isoformat(),
                    "close_price": item.close_price,
                }
            )


def _download_price_klines(
    destination: Path,
    *,
    config: BasisResearchConfig,
    endpoint_path: str,
    label: str,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> tuple[PricePoint, ...]:
    if destination.exists() and destination.stat().st_size > 0:
        existing = _read_price_points(destination)
        if existing:
            print(f"Reuse {label} dataset: {destination}")
            return existing

    start_ms = int(evaluation_start.timestamp() * 1000)
    next_end_ms = int((evaluation_end - timedelta(milliseconds=1)).timestamp() * 1000)
    rows_by_timestamp: dict[datetime, PricePoint] = {}
    page = 0

    while next_end_ms >= start_ms:
        page += 1
        params = {
            "category": "linear",
            "symbol": config.symbol,
            "interval": config.interval,
            "start": start_ms,
            "end": next_end_ms,
            "limit": 1000,
        }
        url = (
            f"{config.endpoint.rstrip('/')}{endpoint_path}?"
            f"{urllib.parse.urlencode(params)}"
        )
        print(f"Download {label} page {page}")
        with _http_request(url, timeout=30.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(
                f"Bybit {label} request failed: "
                f"retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}"
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError(f"Bybit {label} result must be an object")
        raw_list = result.get("list")
        if not isinstance(raw_list, list) or not raw_list:
            break

        oldest_ms: int | None = None
        for raw in raw_list:
            if not isinstance(raw, list) or len(raw) < 5:
                continue
            timestamp_ms = int(raw[0])
            if timestamp_ms < start_ms or timestamp_ms > next_end_ms:
                continue
            timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)
            rows_by_timestamp[timestamp] = PricePoint(
                timestamp=timestamp,
                close_price=Decimal(str(raw[4])),
            )
            oldest_ms = timestamp_ms if oldest_ms is None else min(oldest_ms, timestamp_ms)

        if oldest_ms is None or oldest_ms <= start_ms or len(raw_list) < 1000:
            break
        next_end_ms = oldest_ms - 1
        if config.request_pause_seconds:
            time.sleep(config.request_pause_seconds)

    rows = tuple(sorted(rows_by_timestamp.values(), key=lambda item: item.timestamp))
    if not rows:
        raise RuntimeError(f"no {label} rows were downloaded")
    _write_price_points(destination, rows)
    return rows


def _build_basis_series(
    mark_rows: tuple[PricePoint, ...],
    index_rows: tuple[PricePoint, ...],
) -> BasisSeriesIndex:
    index_by_time = {item.timestamp: item for item in index_rows}
    points: list[BasisPoint] = []
    for mark in mark_rows:
        index = index_by_time.get(mark.timestamp)
        if index is None or index.close_price <= 0:
            continue
        basis_bps = (mark.close_price / index.close_price - Decimal("1")) * Decimal(
            "10000"
        )
        points.append(
            BasisPoint(
                timestamp=mark.timestamp,
                mark_price=mark.close_price,
                index_price=index.close_price,
                basis_bps=basis_bps,
            )
        )
    points.sort(key=lambda item: item.timestamp)
    return BasisSeriesIndex(
        timestamps=tuple(item.timestamp for item in points),
        points=tuple(points),
    )


def _directional_value(value: Decimal, direction: Direction) -> Decimal:
    return value if direction == "Long" else -value


def _directional_change(
    series: BasisSeriesIndex,
    *,
    direction: Direction,
    anchor: BasisPoint,
    window_minutes: int,
) -> Decimal | None:
    previous = series.point_at_or_before(anchor.timestamp - timedelta(minutes=window_minutes))
    if previous is None:
        return None
    raw = anchor.basis_bps - previous.basis_bps
    return _directional_value(raw, direction)


def _basis_state(directional_basis_bps: Decimal | None) -> str:
    if directional_basis_bps is None:
        return "missing"
    if directional_basis_bps > 0:
        return "aligned_premium"
    if directional_basis_bps < 0:
        return "opposed_discount"
    return "flat"


def _quartile_bounds(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    if len(values) < 4:
        raise ValueError("at least four values are required for quartiles")
    ordered = sorted(values)
    quantiles = statistics.quantiles(ordered, n=4, method="inclusive")
    return cast(tuple[Decimal, Decimal, Decimal], tuple(quantiles))


def _quartile(value: Decimal, bounds: tuple[Decimal, Decimal, Decimal]) -> Quartile:
    q1, q2, q3 = bounds
    if value <= q1:
        return "Q1"
    if value <= q2:
        return "Q2"
    if value <= q3:
        return "Q3"
    return "Q4"


def _oi_tail_thresholds(summary: dict[str, Any]) -> tuple[Decimal, Decimal]:
    section = summary.get("p34_oi_tail_recheck")
    if not isinstance(section, dict):
        raise ValueError("P35 summary has no P34 OI-tail section")
    thresholds = section.get("thresholds")
    if not isinstance(thresholds, dict):
        raise ValueError("P35 summary has no OI-tail thresholds")
    high = Decimal(str(thresholds["high_oi_change_60m_pct"]))
    low = Decimal(str(thresholds["low_oi_acceleration_5_vs_60"]))
    return high, low


def _oi_tail_danger(
    signal: P35Signal,
    *,
    high_oi_change: Decimal,
    low_oi_acceleration: Decimal,
) -> bool:
    high = signal.oi_change_60m_pct
    accel = signal.oi_acceleration_5_vs_60
    return (high is not None and high >= high_oi_change) or (
        accel is not None and accel <= low_oi_acceleration
    )


def _annotate(
    signal: P35Signal,
    *,
    series: BasisSeriesIndex,
    high_oi_change: Decimal,
    low_oi_acceleration: Decimal,
) -> AnnotatedBasisSignal:
    anchor = series.point_strictly_before(signal.candidate_bar_at)
    if anchor is None:
        return AnnotatedBasisSignal(
            source=signal,
            basis_anchor_at=None,
            mark_price=None,
            index_price=None,
            basis_bps=None,
            directional_basis_bps=None,
            directional_change_5m_bps=None,
            directional_change_15m_bps=None,
            directional_change_30m_bps=None,
            directional_change_60m_bps=None,
            basis_acceleration_5_vs_60_bps=None,
            basis_state="missing",
            oi_tail_danger=_oi_tail_danger(
                signal,
                high_oi_change=high_oi_change,
                low_oi_acceleration=low_oi_acceleration,
            ),
        )

    changes = {
        minutes: _directional_change(
            series,
            direction=signal.direction,
            anchor=anchor,
            window_minutes=minutes,
        )
        for minutes in (5, 15, 30, 60)
    }
    change5 = changes[5]
    change60 = changes[60]
    acceleration = None
    if change5 is not None and change60 is not None:
        acceleration = change5 - change60 / Decimal("12")

    directional_basis = _directional_value(anchor.basis_bps, signal.direction)
    return AnnotatedBasisSignal(
        source=signal,
        basis_anchor_at=anchor.timestamp,
        mark_price=anchor.mark_price,
        index_price=anchor.index_price,
        basis_bps=anchor.basis_bps,
        directional_basis_bps=directional_basis,
        directional_change_5m_bps=changes[5],
        directional_change_15m_bps=changes[15],
        directional_change_30m_bps=changes[30],
        directional_change_60m_bps=changes[60],
        basis_acceleration_5_vs_60_bps=acceleration,
        basis_state=_basis_state(directional_basis),
        oi_tail_danger=_oi_tail_danger(
            signal,
            high_oi_change=high_oi_change,
            low_oi_acceleration=low_oi_acceleration,
        ),
    )


def _assign_quartiles(
    rows: tuple[AnnotatedBasisSignal, ...],
) -> tuple[AnnotatedBasisSignal, ...]:
    accepted = [item for item in rows if item.source.accepted_after_failure_embargo]
    fields = (
        "directional_basis_bps",
        "directional_change_60m_bps",
        "basis_acceleration_5_vs_60_bps",
    )
    bounds: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    for field in fields:
        values = [
            cast(Decimal, getattr(item, field))
            for item in accepted
            if getattr(item, field) is not None
        ]
        if len(values) >= 4:
            bounds[field] = _quartile_bounds(values)

    result: list[AnnotatedBasisSignal] = []
    for item in rows:
        result.append(
            replace(
                item,
                basis_level_quartile=(
                    None
                    if item.directional_basis_bps is None
                    or "directional_basis_bps" not in bounds
                    else _quartile(
                        item.directional_basis_bps,
                        bounds["directional_basis_bps"],
                    )
                ),
                basis_change_60m_quartile=(
                    None
                    if item.directional_change_60m_bps is None
                    or "directional_change_60m_bps" not in bounds
                    else _quartile(
                        item.directional_change_60m_bps,
                        bounds["directional_change_60m_bps"],
                    )
                ),
                basis_accel_quartile=(
                    None
                    if item.basis_acceleration_5_vs_60_bps is None
                    or "basis_acceleration_5_vs_60_bps" not in bounds
                    else _quartile(
                        item.basis_acceleration_5_vs_60_bps,
                        bounds["basis_acceleration_5_vs_60_bps"],
                    )
                ),
            )
        )
    return tuple(result)


def _percent(numerator: int, denominator: int) -> float:
    return 0.0 if denominator <= 0 else round(numerator / denominator * 100.0, 2)


def _metrics(rows: list[AnnotatedBasisSignal]) -> dict[str, int | float]:
    count = len(rows)
    favorable_05 = sum(
        item.source.first_0_5_vs_1_0 == "favorable_first" for item in rows
    )
    adverse_05 = sum(item.source.first_0_5_vs_1_0 == "adverse_first" for item in rows)
    favorable_10 = sum(
        item.source.first_1_0_vs_1_0 == "favorable_first" for item in rows
    )
    return {
        "signals": count,
        "plus_0_5_before_minus_1_percent": _percent(favorable_05, count),
        "minus_1_before_plus_0_5_percent": _percent(adverse_05, count),
        "plus_1_before_minus_1_percent": _percent(favorable_10, count),
    }


def _group_rows(
    rows: tuple[AnnotatedBasisSignal, ...],
    *,
    field: str,
    label: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[AnnotatedBasisSignal]] = {}
    for item in rows:
        if not item.source.accepted_after_failure_embargo:
            continue
        value = getattr(item, field)
        if value is None:
            continue
        groups.setdefault(str(value), []).append(item)
    output: list[dict[str, Any]] = []
    for value, items in sorted(groups.items()):
        output.append({"feature": label, "value": value, **_metrics(items)})
    return output


def _interaction_rows(
    rows: tuple[AnnotatedBasisSignal, ...],
    *,
    minimum_signals: int,
) -> list[dict[str, Any]]:
    accepted = [item for item in rows if item.source.accepted_after_failure_embargo]
    definitions = (
        ("flow_x_basis_accel", "flow_state", "basis_accel_quartile"),
        ("flow_x_basis_level", "flow_state", "basis_level_quartile"),
        ("oi_tail_x_basis_accel", "oi_tail_danger", "basis_accel_quartile"),
    )
    output: list[dict[str, Any]] = []
    for interaction, left_field, right_field in definitions:
        groups: dict[tuple[str, str], list[AnnotatedBasisSignal]] = {}
        for item in accepted:
            left = (
                str(getattr(item.source, left_field))
                if hasattr(item.source, left_field)
                else str(getattr(item, left_field))
            )
            right_value = getattr(item, right_field)
            if right_value is None:
                continue
            groups.setdefault((left, str(right_value)), []).append(item)
        for (left, right), items in sorted(groups.items()):
            if len(items) < minimum_signals:
                continue
            output.append(
                {
                    "interaction": interaction,
                    "left": left,
                    "right": right,
                    **_metrics(items),
                }
            )

    core = [
        item
        for item in accepted
        if item.source.flow_state == "pressure_then_reversal"
        and not item.oi_tail_danger
    ]
    core_groups: dict[str, list[AnnotatedBasisSignal]] = {}
    for item in core:
        if item.basis_accel_quartile is None:
            continue
        core_groups.setdefault(item.basis_accel_quartile, []).append(item)
    for quartile, items in sorted(core_groups.items()):
        output.append(
            {
                "interaction": "core_flow_no_oi_x_basis_accel",
                "left": "pressure_then_reversal_no_oi_tail",
                "right": quartile,
                **_metrics(items),
            }
        )
    return output


def _monthly_rows(
    rows: tuple[AnnotatedBasisSignal, ...],
    *,
    evaluation_start: datetime,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for segment in range(1, 4):
        segment_start = evaluation_start + timedelta(days=(segment - 1) * 30)
        segment_end = segment_start + timedelta(days=30)
        accepted = [
            item
            for item in rows
            if item.source.accepted_after_failure_embargo
            and segment_start <= item.source.candidate_bar_at < segment_end
        ]
        output.append({"segment": segment, "scope": "accepted", **_metrics(accepted)})
        for quartile in ("Q1", "Q2", "Q3", "Q4"):
            group = [item for item in accepted if item.basis_accel_quartile == quartile]
            output.append(
                {
                    "segment": segment,
                    "scope": f"basis_accel_{quartile}",
                    **_metrics(group),
                }
            )
        core = [
            item
            for item in accepted
            if item.source.flow_state == "pressure_then_reversal"
            and not item.oi_tail_danger
        ]
        output.append({"segment": segment, "scope": "core_flow_no_oi", **_metrics(core)})
    return output


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return _decimal_json(value)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _write_signals(path: Path, rows: tuple[AnnotatedBasisSignal, ...]) -> None:
    output: list[dict[str, Any]] = []
    for item in rows:
        row = asdict(item.source)
        row.update(
            {
                "basis_anchor_at": item.basis_anchor_at,
                "mark_price": item.mark_price,
                "index_price": item.index_price,
                "basis_bps": item.basis_bps,
                "directional_basis_bps": item.directional_basis_bps,
                "directional_change_5m_bps": item.directional_change_5m_bps,
                "directional_change_15m_bps": item.directional_change_15m_bps,
                "directional_change_30m_bps": item.directional_change_30m_bps,
                "directional_change_60m_bps": item.directional_change_60m_bps,
                "basis_acceleration_5_vs_60_bps": (
                    item.basis_acceleration_5_vs_60_bps
                ),
                "basis_state": item.basis_state,
                "oi_tail_danger": item.oi_tail_danger,
                "basis_level_quartile": item.basis_level_quartile,
                "basis_change_60m_quartile": item.basis_change_60m_quartile,
                "basis_accel_quartile": item.basis_accel_quartile,
            }
        )
        output.append(row)
    _write_rows(path, output)


def _build_summary(
    rows: tuple[AnnotatedBasisSignal, ...],
    *,
    config: BasisResearchConfig,
    dataset_dir: Path,
    p35_dir: Path,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> dict[str, Any]:
    accepted = [item for item in rows if item.source.accepted_after_failure_embargo]
    core = [
        item
        for item in accepted
        if item.source.flow_state == "pressure_then_reversal"
        and not item.oi_tail_danger
    ]
    return {
        "architecture": "p36_perpetual_basis_context",
        "dataset_dir": dataset_dir,
        "p35_dir": p35_dir,
        "evaluation_start": evaluation_start.isoformat(),
        "evaluation_end": evaluation_end.isoformat(),
        "config": asdict(config),
        "risk_convention": {
            "price_invalidation_percent_at_1x": "1.0",
            "meaning": "price move from entry; leverage scales allowed price move separately",
            "live_risk_engine_changed": False,
        },
        "accepted_after_60m_invalidation_pause": _metrics(accepted),
        "core_pressure_reversal_without_oi_tail": _metrics(core),
        "basis_state": _group_rows(rows, field="basis_state", label="basis_state"),
        "basis_level_quartiles": _group_rows(
            rows,
            field="basis_level_quartile",
            label="directional_basis_quartile",
        ),
        "basis_change_60m_quartiles": _group_rows(
            rows,
            field="basis_change_60m_quartile",
            label="directional_basis_change_60m_quartile",
        ),
        "basis_acceleration_quartiles": _group_rows(
            rows,
            field="basis_accel_quartile",
            label="basis_acceleration_quartile",
        ),
        "notes": [
            "P36 changes no live trading, stop-loss, take-profit, or exit logic.",
            "Mark/index data are anchored to the last fully closed 5m bar before candidate time.",
            "Basis features are diagnostic only; no quartile becomes an entry gate.",
            "Static long/short account majority remains a weak context after P35.",
            "P36 focuses on current perpetual-vs-index pressure rather than holder counts.",
        ],
    }


def run_basis_research(
    *,
    config: BasisResearchConfig,
    p35_dir: Path,
    dataset_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    p35_summary_path = p35_dir / "summary.json"
    p35_signals_path = p35_dir / "signals_crowding.csv"
    if not p35_summary_path.exists() or not p35_signals_path.exists():
        raise FileNotFoundError("P35 summary/signals are required")

    p35_summary = json.loads(p35_summary_path.read_text(encoding="utf-8-sig"))
    evaluation_start = datetime.fromisoformat(p35_summary["evaluation_start"]).astimezone(UTC)
    evaluation_end = datetime.fromisoformat(p35_summary["evaluation_end"]).astimezone(UTC)
    download_start = evaluation_start - timedelta(hours=2)

    signals = _read_p35_signals(p35_signals_path, symbol=config.symbol)
    high_oi_change, low_oi_acceleration = _oi_tail_thresholds(p35_summary)

    mark_rows = _download_price_klines(
        dataset_dir / "mark_price_5m.csv",
        config=config,
        endpoint_path="/v5/market/mark-price-kline",
        label="mark-price 5m",
        evaluation_start=download_start,
        evaluation_end=evaluation_end,
    )
    index_rows = _download_price_klines(
        dataset_dir / "index_price_5m.csv",
        config=config,
        endpoint_path="/v5/market/index-price-kline",
        label="index-price 5m",
        evaluation_start=download_start,
        evaluation_end=evaluation_end,
    )
    series = _build_basis_series(mark_rows, index_rows)
    if not series.points:
        raise RuntimeError("mark/index datasets have no overlapping timestamps")

    annotated = tuple(
        _annotate(
            signal,
            series=series,
            high_oi_change=high_oi_change,
            low_oi_acceleration=low_oi_acceleration,
        )
        for signal in signals
    )
    annotated = _assign_quartiles(annotated)

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_signals(output_dir / "signals_basis.csv", annotated)
    _write_rows(
        output_dir / "basis_quartiles.csv",
        _group_rows(
            annotated,
            field="basis_level_quartile",
            label="directional_basis_quartile",
        )
        + _group_rows(
            annotated,
            field="basis_change_60m_quartile",
            label="directional_basis_change_60m_quartile",
        )
        + _group_rows(
            annotated,
            field="basis_accel_quartile",
            label="basis_acceleration_quartile",
        ),
    )
    _write_rows(
        output_dir / "basis_interactions.csv",
        _interaction_rows(
            annotated,
            minimum_signals=config.minimum_interaction_signals,
        ),
    )
    _write_rows(
        output_dir / "monthly_stability.csv",
        _monthly_rows(annotated, evaluation_start=evaluation_start),
    )
    summary = _build_summary(
        annotated,
        config=config,
        dataset_dir=dataset_dir,
        p35_dir=p35_dir,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )
    _write_json(output_dir / "summary.json", summary)

    print(f"Dataset: {dataset_dir}")
    print(f"P35 source: {p35_dir}")
    print(f"Signals analysed: {len(annotated)}")
    print(
        "P36 accepted after 60m pause: "
        f"{sum(item.source.accepted_after_failure_embargo for item in annotated)}"
    )
    print(f"Report: {output_dir / 'summary.json'}")
    return summary


def _default_output_dir(symbol: str) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    return Path("reports") / "entry_research_v9" / f"{symbol}_{stamp}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P36 perpetual-basis entry context research")
    parser.add_argument("--symbol", default="UNIUSDT")
    parser.add_argument("--endpoint", default="https://api.bybit.kz")
    parser.add_argument("--p35-dir", required=True)
    parser.add_argument("--dataset-dir", default="")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args(argv)

    p35_dir = Path(args.p35_dir)
    p35_summary = json.loads((p35_dir / "summary.json").read_text(encoding="utf-8-sig"))
    dataset_dir = (
        Path(args.dataset_dir)
        if args.dataset_dir
        else Path(str(p35_summary["dataset_dir"]))
    )
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(args.symbol)

    run_basis_research(
        config=BasisResearchConfig(symbol=args.symbol, endpoint=args.endpoint),
        p35_dir=p35_dir,
        dataset_dir=dataset_dir,
        output_dir=output_dir,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
