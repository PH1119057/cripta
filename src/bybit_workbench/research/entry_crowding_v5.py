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
from bybit_workbench.research.research_http import read_json_with_retry

Direction = Literal["Long", "Short"]
Outcome = Literal["favorable_first", "adverse_first", "neither"]
Quartile = Literal["Q1", "Q2", "Q3", "Q4"]


@dataclass(frozen=True, slots=True)
class CrowdingResearchConfig:
    symbol: str = "UNIUSDT"
    endpoint: str = "https://api.bybit.kz"
    period: str = "5min"
    windows_minutes: tuple[int, ...] = (5, 15, 30, 60)
    minimum_interaction_signals: int = 20
    request_pause_seconds: float = 0.15

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if not self.endpoint.startswith("https://"):
            raise ValueError("endpoint must use https")
        if self.period != "5min":
            raise ValueError("P35 currently requires 5min account-ratio data")
        if not self.windows_minutes or any(value <= 0 for value in self.windows_minutes):
            raise ValueError("crowding windows must be positive")
        if self.minimum_interaction_signals <= 0:
            raise ValueError("minimum interaction size must be positive")
        if self.request_pause_seconds < 0:
            raise ValueError("request pause cannot be negative")


@dataclass(frozen=True, slots=True)
class P34Signal:
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
    oi_60m_quartile: str
    oi_accel_quartile: str


@dataclass(frozen=True, slots=True)
class P33PathMetrics:
    exact_mae_30m_pct: Decimal | None
    seconds_to_plus_0_5: float | None
    seconds_to_plus_1_0: float | None


@dataclass(frozen=True, slots=True)
class AccountRatioPoint:
    timestamp: datetime
    buy_ratio: Decimal
    sell_ratio: Decimal


@dataclass(frozen=True, slots=True)
class RatioSeriesIndex:
    timestamps: tuple[datetime, ...]
    points: tuple[AccountRatioPoint, ...]

    def point_at_or_before(self, timestamp: datetime) -> AccountRatioPoint | None:
        index = bisect.bisect_right(self.timestamps, timestamp) - 1
        if index < 0:
            return None
        return self.points[index]


@dataclass(frozen=True, slots=True)
class AnnotatedCrowdingSignal:
    source: P34Signal
    path: P33PathMetrics
    ratio_anchor_at: datetime | None
    buy_ratio_pct: Decimal | None
    sell_ratio_pct: Decimal | None
    directional_crowd_share_pct: Decimal | None
    directional_crowd_edge_pct: Decimal | None
    directional_change_5m_pct_points: Decimal | None
    directional_change_15m_pct_points: Decimal | None
    directional_change_30m_pct_points: Decimal | None
    directional_change_60m_pct_points: Decimal | None
    crowd_acceleration_5_vs_60: Decimal | None
    crowd_majority: str
    crowd_edge_quartile: Quartile | None = None
    crowd_change_60m_quartile: Quartile | None = None
    crowd_accel_quartile: Quartile | None = None


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
    return None if not text else float(text)


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


def _read_p34_signals(path: Path, *, symbol: str) -> tuple[P34Signal, ...]:
    rows: list[P34Signal] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("symbol") != symbol:
                continue
            rows.append(
                P34Signal(
                    symbol=symbol,
                    direction=_parse_direction(raw["direction"]),
                    candidate_bar_at=datetime.fromisoformat(raw["candidate_bar_at"]).astimezone(
                        UTC
                    ),
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
                    oi_60m_quartile=raw["oi_60m_quartile"],
                    oi_accel_quartile=raw["oi_accel_quartile"],
                )
            )
    return tuple(rows)


def _signal_key(direction: Direction, touch_at: datetime, entry_price: Decimal) -> str:
    return f"{direction}|{touch_at.isoformat()}|{entry_price}"


def _read_p33_paths(path: Path, *, symbol: str) -> dict[str, P33PathMetrics]:
    rows: dict[str, P33PathMetrics] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("symbol") != symbol:
                continue
            direction = _parse_direction(raw["direction"])
            touch_at = datetime.fromisoformat(raw["touch_at"]).astimezone(UTC)
            entry_price = Decimal(raw["entry_price"])
            rows[_signal_key(direction, touch_at, entry_price)] = P33PathMetrics(
                exact_mae_30m_pct=_parse_decimal(raw["exact_mae_30m_pct"]),
                seconds_to_plus_0_5=_parse_float(raw["seconds_to_plus_0_5"]),
                seconds_to_plus_1_0=_parse_float(raw["seconds_to_plus_1_0"]),
            )
    return rows


def _read_account_ratio(path: Path) -> tuple[AccountRatioPoint, ...]:
    rows: list[AccountRatioPoint] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for raw in csv.DictReader(handle):
            rows.append(
                AccountRatioPoint(
                    timestamp=datetime.fromisoformat(raw["timestamp"]).astimezone(UTC),
                    buy_ratio=Decimal(raw["buy_ratio"]),
                    sell_ratio=Decimal(raw["sell_ratio"]),
                )
            )
    rows.sort(key=lambda item: item.timestamp)
    return tuple(rows)


def _write_account_ratio(path: Path, rows: tuple[AccountRatioPoint, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["timestamp", "buy_ratio", "sell_ratio", "long_short_ratio"],
        )
        writer.writeheader()
        for item in rows:
            ratio = None
            if item.sell_ratio > 0:
                ratio = item.buy_ratio / item.sell_ratio
            writer.writerow(
                {
                    "timestamp": item.timestamp.isoformat(),
                    "buy_ratio": item.buy_ratio,
                    "sell_ratio": item.sell_ratio,
                    "long_short_ratio": ratio,
                }
            )


def _download_account_ratio(
    destination: Path,
    *,
    config: CrowdingResearchConfig,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> tuple[AccountRatioPoint, ...]:
    if destination.exists() and destination.stat().st_size > 0:
        existing = _read_account_ratio(destination)
        if existing:
            print(f"Reuse account-ratio dataset: {destination}")
            return existing

    rows_by_timestamp: dict[datetime, AccountRatioPoint] = {}
    cursor = ""
    seen_cursors: set[str] = set()
    page = 0
    while True:
        page += 1
        params: dict[str, str | int] = {
            "category": "linear",
            "symbol": config.symbol,
            "period": config.period,
            "startTime": int(evaluation_start.timestamp() * 1000),
            "endTime": int((evaluation_end - timedelta(milliseconds=1)).timestamp() * 1000),
            "limit": 500,
        }
        if cursor:
            params["cursor"] = cursor
        url = (
            f"{config.endpoint.rstrip('/')}/v5/market/account-ratio?"
            f"{urllib.parse.urlencode(params)}"
        )
        print(f"AUX REST long/short account ratio page {page}", flush=True)
        payload = read_json_with_retry(
            url,
            label=f"{config.symbol} account-ratio page {page}",
            timeout=30.0,
        )
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(
                "Bybit account-ratio request failed: "
                f"retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}"
            )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise ValueError("Bybit account-ratio result is missing")
        raw_list = result.get("list")
        if not isinstance(raw_list, list):
            raise ValueError("Bybit account-ratio list is missing")
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            timestamp = datetime.fromtimestamp(int(str(raw["timestamp"])) / 1000, tz=UTC)
            if timestamp < evaluation_start or timestamp >= evaluation_end:
                continue
            rows_by_timestamp[timestamp] = AccountRatioPoint(
                timestamp=timestamp,
                buy_ratio=Decimal(str(raw["buyRatio"])),
                sell_ratio=Decimal(str(raw["sellRatio"])),
            )
        next_cursor = str(result.get("nextPageCursor") or "")
        if not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
        if config.request_pause_seconds:
            time.sleep(config.request_pause_seconds)
        if page >= 200:
            raise RuntimeError("account-ratio pagination exceeded 200 pages")

    rows = tuple(rows_by_timestamp[key] for key in sorted(rows_by_timestamp))
    if not rows:
        raise ValueError("Bybit returned no account-ratio rows for the frozen interval")
    _write_account_ratio(destination, rows)
    return rows


def _series(rows: tuple[AccountRatioPoint, ...]) -> RatioSeriesIndex:
    return RatioSeriesIndex(
        timestamps=tuple(item.timestamp for item in rows),
        points=rows,
    )


def _directional_share(point: AccountRatioPoint, direction: Direction) -> Decimal:
    ratio = point.buy_ratio if direction == "Long" else point.sell_ratio
    return ratio * Decimal("100")


def _directional_edge(point: AccountRatioPoint, direction: Direction) -> Decimal:
    if direction == "Long":
        return (point.buy_ratio - point.sell_ratio) * Decimal("100")
    return (point.sell_ratio - point.buy_ratio) * Decimal("100")


def _directional_change(
    series: RatioSeriesIndex,
    *,
    direction: Direction,
    anchor_at: datetime,
    window_minutes: int,
) -> Decimal | None:
    current = series.point_at_or_before(anchor_at)
    previous = series.point_at_or_before(anchor_at - timedelta(minutes=window_minutes))
    if current is None or previous is None:
        return None
    return _directional_share(current, direction) - _directional_share(previous, direction)


def _crowd_majority(edge: Decimal | None) -> str:
    if edge is None:
        return "missing"
    if edge > 0:
        return "aligned_majority"
    if edge < 0:
        return "opposed_majority"
    return "balanced"


def _quartile_bounds(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    if len(values) < 4:
        raise ValueError("at least four values are required for quartiles")
    q1, q2, q3 = statistics.quantiles(
        [float(value) for value in values], n=4, method="inclusive"
    )
    return Decimal(str(q1)), Decimal(str(q2)), Decimal(str(q3))


def _quartile(value: Decimal | None, bounds: tuple[Decimal, Decimal, Decimal]) -> Quartile | None:
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


def _annotate(
    signals: tuple[P34Signal, ...],
    paths: dict[str, P33PathMetrics],
    ratios: RatioSeriesIndex,
    *,
    config: CrowdingResearchConfig,
) -> tuple[AnnotatedCrowdingSignal, ...]:
    rows: list[AnnotatedCrowdingSignal] = []
    for signal in signals:
        path = paths.get(_signal_key(signal.direction, signal.touch_at, signal.entry_price))
        if path is None:
            raise ValueError(f"P33 path not found for {signal.touch_at.isoformat()}")
        anchor = ratios.point_at_or_before(signal.candidate_bar_at)
        changes = {
            minutes: _directional_change(
                ratios,
                direction=signal.direction,
                anchor_at=signal.candidate_bar_at,
                window_minutes=minutes,
            )
            for minutes in config.windows_minutes
        }
        edge = None if anchor is None else _directional_edge(anchor, signal.direction)
        share = None if anchor is None else _directional_share(anchor, signal.direction)
        buy_pct = None if anchor is None else anchor.buy_ratio * Decimal("100")
        sell_pct = None if anchor is None else anchor.sell_ratio * Decimal("100")
        acceleration: Decimal | None = None
        if changes.get(5) is not None and changes.get(60) is not None:
            acceleration = cast(Decimal, changes[5]) - cast(Decimal, changes[60]) / Decimal("12")
        rows.append(
            AnnotatedCrowdingSignal(
                source=signal,
                path=path,
                ratio_anchor_at=None if anchor is None else anchor.timestamp,
                buy_ratio_pct=buy_pct,
                sell_ratio_pct=sell_pct,
                directional_crowd_share_pct=share,
                directional_crowd_edge_pct=edge,
                directional_change_5m_pct_points=changes.get(5),
                directional_change_15m_pct_points=changes.get(15),
                directional_change_30m_pct_points=changes.get(30),
                directional_change_60m_pct_points=changes.get(60),
                crowd_acceleration_5_vs_60=acceleration,
                crowd_majority=_crowd_majority(edge),
            )
        )
    return _assign_quartiles(tuple(rows))


def _assign_quartiles(
    signals: tuple[AnnotatedCrowdingSignal, ...],
) -> tuple[AnnotatedCrowdingSignal, ...]:
    edge_values = [
        item.directional_crowd_edge_pct
        for item in signals
        if item.directional_crowd_edge_pct is not None
    ]
    change_values = [
        item.directional_change_60m_pct_points
        for item in signals
        if item.directional_change_60m_pct_points is not None
    ]
    accel_values = [
        item.crowd_acceleration_5_vs_60
        for item in signals
        if item.crowd_acceleration_5_vs_60 is not None
    ]
    if min(len(edge_values), len(change_values), len(accel_values)) < 4:
        return signals
    edge_bounds = _quartile_bounds(edge_values)
    change_bounds = _quartile_bounds(change_values)
    accel_bounds = _quartile_bounds(accel_values)
    return tuple(
        replace(
            item,
            crowd_edge_quartile=_quartile(item.directional_crowd_edge_pct, edge_bounds),
            crowd_change_60m_quartile=_quartile(
                item.directional_change_60m_pct_points, change_bounds
            ),
            crowd_accel_quartile=_quartile(item.crowd_acceleration_5_vs_60, accel_bounds),
        )
        for item in signals
    )


def _percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator * 100.0 / denominator, 2)


def _metrics(signals: list[AnnotatedCrowdingSignal]) -> dict[str, Any]:
    total = len(signals)
    favorable_half = sum(
        item.source.first_0_5_vs_1_0 == "favorable_first" for item in signals
    )
    adverse_half = sum(
        item.source.first_0_5_vs_1_0 == "adverse_first" for item in signals
    )
    favorable_one = sum(
        item.source.first_1_0_vs_1_0 == "favorable_first" for item in signals
    )
    hit_half = sum(item.path.seconds_to_plus_0_5 is not None for item in signals)
    hit_one = sum(item.path.seconds_to_plus_1_0 is not None for item in signals)
    minus_one_30m = sum(
        item.path.exact_mae_30m_pct is not None
        and item.path.exact_mae_30m_pct <= Decimal("-1")
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
    signals: tuple[AnnotatedCrowdingSignal, ...],
    *,
    feature: str,
    getter: Any,
    minimum_signals: int = 1,
) -> list[dict[str, Any]]:
    groups: dict[str, list[AnnotatedCrowdingSignal]] = {}
    for item in signals:
        value = getter(item)
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
    signals: tuple[AnnotatedCrowdingSignal, ...],
    *,
    evaluation_start: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for segment in range(3):
        start = evaluation_start + timedelta(days=30 * segment)
        end = start + timedelta(days=30)
        month = tuple(item for item in signals if start <= item.source.touch_at < end)
        rows.append({"segment": segment + 1, "scope": "all", **_metrics(list(month))})
        accepted = tuple(item for item in month if item.source.accepted_after_failure_embargo)
        rows.append(
            {
                "segment": segment + 1,
                "scope": "accepted_after_60m_invalidation_pause",
                **_metrics(list(accepted)),
            }
        )
        for quartile in ("Q1", "Q2", "Q3", "Q4"):
            edge_group = [item for item in accepted if item.crowd_edge_quartile == quartile]
            rows.append(
                {
                    "segment": segment + 1,
                    "scope": f"accepted_crowd_edge_{quartile}",
                    **_metrics(edge_group),
                }
            )
            accel_group = [item for item in accepted if item.crowd_accel_quartile == quartile]
            rows.append(
                {
                    "segment": segment + 1,
                    "scope": f"accepted_crowd_accel_{quartile}",
                    **_metrics(accel_group),
                }
            )
    return rows


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return _decimal_json(value)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default) + "\n",
        encoding="utf-8",
    )


def _write_signals(path: Path, signals: tuple[AnnotatedCrowdingSignal, ...]) -> None:
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
        "oi_change_60m_pct",
        "oi_acceleration_5_vs_60",
        "oi_60m_quartile",
        "oi_accel_quartile",
        "ratio_anchor_at",
        "buy_ratio_pct",
        "sell_ratio_pct",
        "directional_crowd_share_pct",
        "directional_crowd_edge_pct",
        "directional_change_5m_pct_points",
        "directional_change_15m_pct_points",
        "directional_change_30m_pct_points",
        "directional_change_60m_pct_points",
        "crowd_acceleration_5_vs_60",
        "crowd_majority",
        "crowd_edge_quartile",
        "crowd_change_60m_quartile",
        "crowd_accel_quartile",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in signals:
            source = item.source
            writer.writerow(
                {
                    "symbol": source.symbol,
                    "direction": source.direction,
                    "candidate_bar_at": source.candidate_bar_at.isoformat(),
                    "touch_at": source.touch_at.isoformat(),
                    "entry_price": source.entry_price,
                    "hourly_alignment": source.hourly_alignment,
                    "flow_state": source.flow_state,
                    "first_0_5_vs_1_0": source.first_0_5_vs_1_0,
                    "first_1_0_vs_1_0": source.first_1_0_vs_1_0,
                    "accepted_after_failure_embargo": source.accepted_after_failure_embargo,
                    "oi_change_60m_pct": source.oi_change_60m_pct,
                    "oi_acceleration_5_vs_60": source.oi_acceleration_5_vs_60,
                    "oi_60m_quartile": source.oi_60m_quartile,
                    "oi_accel_quartile": source.oi_accel_quartile,
                    "ratio_anchor_at": (
                        None if item.ratio_anchor_at is None else item.ratio_anchor_at.isoformat()
                    ),
                    "buy_ratio_pct": item.buy_ratio_pct,
                    "sell_ratio_pct": item.sell_ratio_pct,
                    "directional_crowd_share_pct": item.directional_crowd_share_pct,
                    "directional_crowd_edge_pct": item.directional_crowd_edge_pct,
                    "directional_change_5m_pct_points": item.directional_change_5m_pct_points,
                    "directional_change_15m_pct_points": item.directional_change_15m_pct_points,
                    "directional_change_30m_pct_points": item.directional_change_30m_pct_points,
                    "directional_change_60m_pct_points": item.directional_change_60m_pct_points,
                    "crowd_acceleration_5_vs_60": item.crowd_acceleration_5_vs_60,
                    "crowd_majority": item.crowd_majority,
                    "crowd_edge_quartile": item.crowd_edge_quartile,
                    "crowd_change_60m_quartile": item.crowd_change_60m_quartile,
                    "crowd_accel_quartile": item.crowd_accel_quartile,
                }
            )


def _p34_metadata(p34_dir: Path) -> tuple[Path, Path, datetime, datetime]:
    summary_path = p34_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"P34 summary not found: {summary_path}")
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    dataset_dir = Path(str(payload["dataset_dir"]))
    p33_dir = Path(str(payload["p33_dir"]))
    start = datetime.fromisoformat(str(payload["evaluation_start"])).astimezone(UTC)
    end = datetime.fromisoformat(str(payload["evaluation_end"])).astimezone(UTC)
    return dataset_dir, p33_dir, start, end


def run_crowding_research(
    p34_dir: Path,
    *,
    config: CrowdingResearchConfig,
    dataset_dir_override: Path | None = None,
) -> tuple[tuple[AnnotatedCrowdingSignal, ...], Path, Path, datetime, datetime]:
    dataset_dir, p33_dir, evaluation_start, evaluation_end = _p34_metadata(p34_dir)
    if dataset_dir_override is not None:
        dataset_dir = dataset_dir_override
    p34_signals = _read_p34_signals(p34_dir / "signals_open_interest.csv", symbol=config.symbol)
    paths = _read_p33_paths(p33_dir / "signals_adverse_path.csv", symbol=config.symbol)
    ratio_path = dataset_dir / "account_ratio_5m.csv"
    ratio_rows = _download_account_ratio(
        ratio_path,
        config=config,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )
    annotated = _annotate(p34_signals, paths, _series(ratio_rows), config=config)
    return annotated, dataset_dir, p33_dir, evaluation_start, evaluation_end


def _oi_tail_thresholds(
    signals: tuple[AnnotatedCrowdingSignal, ...],
) -> tuple[Decimal, Decimal] | None:
    oi_values = [
        item.source.oi_change_60m_pct
        for item in signals
        if item.source.oi_change_60m_pct is not None
    ]
    accel_values = [
        item.source.oi_acceleration_5_vs_60
        for item in signals
        if item.source.oi_acceleration_5_vs_60 is not None
    ]
    if len(oi_values) < 10 or len(accel_values) < 10:
        return None
    oi_deciles = statistics.quantiles(
        [float(value) for value in oi_values], n=10, method="inclusive"
    )
    accel_deciles = statistics.quantiles(
        [float(value) for value in accel_values], n=10, method="inclusive"
    )
    high_oi = Decimal(str(oi_deciles[8]))
    low_accel = Decimal(str(accel_deciles[0]))
    return high_oi, low_accel


def _oi_tail_danger(
    item: AnnotatedCrowdingSignal,
    thresholds: tuple[Decimal, Decimal] | None,
) -> bool | None:
    if thresholds is None:
        return None
    oi = item.source.oi_change_60m_pct
    accel = item.source.oi_acceleration_5_vs_60
    if oi is None or accel is None:
        return None
    high_oi, low_accel = thresholds
    return oi >= high_oi or accel <= low_accel


def _oi_tail_rows(
    signals: tuple[AnnotatedCrowdingSignal, ...],
) -> tuple[list[dict[str, Any]], tuple[Decimal, Decimal] | None]:
    accepted = tuple(item for item in signals if item.source.accepted_after_failure_embargo)
    thresholds = _oi_tail_thresholds(accepted)
    rows: list[dict[str, Any]] = []
    for danger_value in (False, True):
        group = [
            item
            for item in accepted
            if _oi_tail_danger(item, thresholds) is danger_value
        ]
        rows.append(
            {
                "scope": "all_accepted",
                "oi_tail_danger": danger_value,
                **_metrics(group),
            }
        )
        reversal = [
            item
            for item in group
            if item.source.flow_state == "pressure_then_reversal"
        ]
        rows.append(
            {
                "scope": "pressure_then_reversal",
                "oi_tail_danger": danger_value,
                **_metrics(reversal),
            }
        )
    return rows, thresholds


def _interaction_rows(
    signals: tuple[AnnotatedCrowdingSignal, ...],
    *,
    config: CrowdingResearchConfig,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    accepted = tuple(item for item in signals if item.source.accepted_after_failure_embargo)
    flow_states = sorted({item.source.flow_state for item in accepted})
    quartiles: tuple[Quartile, ...] = ("Q1", "Q2", "Q3", "Q4")
    for flow_state in flow_states:
        for quartile in quartiles:
            group = [
                item
                for item in accepted
                if item.source.flow_state == flow_state
                and item.crowd_edge_quartile == quartile
            ]
            if len(group) < config.minimum_interaction_signals:
                continue
            rows.append(
                {
                    "interaction": "flow_state_x_crowd_edge",
                    "left": flow_state,
                    "right": quartile,
                    **_metrics(group),
                }
            )
    for oi_quartile in quartiles:
        for crowd_quartile in quartiles:
            group = [
                item
                for item in accepted
                if item.source.oi_60m_quartile == oi_quartile
                and item.crowd_edge_quartile == crowd_quartile
            ]
            if len(group) < config.minimum_interaction_signals:
                continue
            rows.append(
                {
                    "interaction": "oi_60m_x_crowd_edge",
                    "left": oi_quartile,
                    "right": crowd_quartile,
                    **_metrics(group),
                }
            )
    return rows


def _build_summary(
    signals: tuple[AnnotatedCrowdingSignal, ...],
    *,
    config: CrowdingResearchConfig,
    dataset_dir: Path,
    p34_dir: Path,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> dict[str, Any]:
    accepted = tuple(item for item in signals if item.source.accepted_after_failure_embargo)
    oi_tail_rows, oi_tail_thresholds = _oi_tail_rows(signals)
    return {
        "architecture": "p35_long_short_account_crowding_context",
        "dataset_dir": dataset_dir,
        "p34_dir": p34_dir,
        "evaluation_start": evaluation_start,
        "evaluation_end": evaluation_end,
        "config": asdict(config),
        "risk_convention": {
            "price_invalidation_percent_at_1x": Decimal("1.0"),
            "meaning": "price move from entry; leverage scales allowed price move separately",
            "live_risk_engine_changed": False,
        },
        "baseline": _metrics(list(signals)),
        "after_60m_invalidation_pause": _metrics(list(accepted)),
        "p34_oi_tail_recheck": {
            "thresholds": (
                None
                if oi_tail_thresholds is None
                else {
                    "high_oi_change_60m_pct": oi_tail_thresholds[0],
                    "low_oi_acceleration_5_vs_60": oi_tail_thresholds[1],
                }
            ),
            "groups": oi_tail_rows,
            "warning": "full-sample deciles are descriptive, not a live gate",
        },
        "crowd_majority": _group_rows(
            accepted,
            feature="crowd_majority",
            getter=lambda item: item.crowd_majority,
        ),
        "crowd_edge_quartiles": _group_rows(
            accepted,
            feature="directional_crowd_edge_quartile",
            getter=lambda item: item.crowd_edge_quartile,
        ),
        "crowd_change_60m_quartiles": _group_rows(
            accepted,
            feature="directional_change_60m_quartile",
            getter=lambda item: item.crowd_change_60m_quartile,
        ),
        "crowd_acceleration_quartiles": _group_rows(
            accepted,
            feature="crowd_acceleration_quartile",
            getter=lambda item: item.crowd_accel_quartile,
        ),
        "notes": [
            "P35 changes no live trading, stop-loss, take-profit, or exit logic.",
            "Long/short account ratio measures holder counts, not position notional.",
            "All account-ratio features are anchored at or before candidate 5m bar start.",
            "Crowding is diagnostic only; no crowding threshold becomes an entry gate.",
            "The 60m post-invalidation pause remains the causal P33/P34 baseline.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P35 long/short crowding entry research")
    parser.add_argument("--symbol", default="UNIUSDT")
    parser.add_argument("--endpoint", default="https://api.bybit.kz")
    parser.add_argument("--p34-dir", required=True)
    parser.add_argument("--dataset-dir", default="")
    parser.add_argument("--output-dir", default="")
    args = parser.parse_args(argv)

    config = CrowdingResearchConfig(symbol=args.symbol, endpoint=args.endpoint)
    p34_dir = Path(args.p34_dir)
    dataset_override = Path(args.dataset_dir) if args.dataset_dir else None
    signals, dataset_dir, _p33_dir, evaluation_start, evaluation_end = run_crowding_research(
        p34_dir,
        config=config,
        dataset_dir_override=dataset_override,
    )

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        output_dir = Path("reports") / "entry_research_v8" / f"{config.symbol}_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    _write_signals(output_dir / "signals_crowding.csv", signals)
    accepted = tuple(item for item in signals if item.source.accepted_after_failure_embargo)
    quartile_rows = (
        _group_rows(
            accepted,
            feature="directional_crowd_edge_quartile",
            getter=lambda item: item.crowd_edge_quartile,
        )
        + _group_rows(
            accepted,
            feature="directional_change_60m_quartile",
            getter=lambda item: item.crowd_change_60m_quartile,
        )
        + _group_rows(
            accepted,
            feature="crowd_acceleration_quartile",
            getter=lambda item: item.crowd_accel_quartile,
        )
    )
    _write_rows(output_dir / "crowding_quartiles.csv", quartile_rows)
    _write_rows(
        output_dir / "crowding_states.csv",
        _group_rows(
            accepted,
            feature="crowd_majority",
            getter=lambda item: item.crowd_majority,
        ),
    )
    _write_rows(output_dir / "crowding_interactions.csv", _interaction_rows(signals, config=config))
    _write_rows(
        output_dir / "monthly_stability.csv",
        _monthly_rows(signals, evaluation_start=evaluation_start),
    )
    oi_tail_rows, _ = _oi_tail_rows(signals)
    _write_rows(output_dir / "oi_tail_recheck.csv", oi_tail_rows)
    summary = _build_summary(
        signals,
        config=config,
        dataset_dir=dataset_dir,
        p34_dir=p34_dir,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
    )
    _write_json(output_dir / "summary.json", summary)

    print(f"Dataset: {dataset_dir}")
    print(f"P34 source: {p34_dir}")
    print(f"Signals analysed: {len(signals)}")
    print(f"P35 accepted after 60m pause: {len(accepted)}")
    print(f"Report: {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    import sys
    import traceback

    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stdout)
        raise SystemExit(2) from None
