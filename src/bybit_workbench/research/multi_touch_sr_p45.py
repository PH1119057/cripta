from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

Direction = Literal["Long", "Short"]
Outcome = Literal["favorable_first", "adverse_first", "neither"]
Role = Literal["support", "resistance"]
Segment = Literal["S1", "S2", "S3"]
Quartile = Literal["Q1", "Q2", "Q3", "Q4"]

DEFAULT_SYMBOLS: tuple[str, ...] = (
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
DISPLAY_SYMBOLS: dict[str, str] = {"1000PEPEUSDT": "PEPE"}

PIVOT_SPAN = 2
ATR_PERIOD = 200
ZONE_HALF_WIDTH_ATR = 0.5
REARM_DISTANCE_ATR = 1.0
BREAK_CONFIRM_CLOSES = 2
APPROACH_BARS = 8
NEAR_ZONE_ATR = 0.5


@dataclass(frozen=True, slots=True)
class Candle:
    opened_at: datetime
    closed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class CoreSignal:
    symbol: str
    direction: Direction
    touch_at: datetime
    entry_price: float
    outcome_05: Outcome
    outcome_10: Outcome


@dataclass(slots=True)
class Zone:
    zone_id: int
    center: float
    half_width: float
    origin_at: datetime
    confirmed_at: datetime
    origin_role: Role
    role: Role
    source_pivots: int
    support_pivots: int
    resistance_pivots: int
    retest_count: int = 0
    last_retest_at: datetime | None = None
    role_flips: int = 0
    last_role_flip_at: datetime | None = None
    false_breaks: int = 0
    pending_break_closes: int = 0
    armed_for_retest: bool = False
    rejection_max_atr: float = 0.0

    @property
    def lower(self) -> float:
        return self.center - self.half_width

    @property
    def upper(self) -> float:
        return self.center + self.half_width


@dataclass(frozen=True, slots=True)
class TouchEvent:
    symbol: str
    zone_id: int
    role: Role
    event_at: datetime
    retest_ordinal: int
    source_pivots: int
    role_flips: int
    false_breaks: int
    center: float
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class ZoneFeatureRow:
    symbol: str
    display_symbol: str
    direction: Direction
    touch_at: datetime
    segment: Segment
    entry_price: float
    outcome_05: Outcome
    outcome_10: Outcome
    zone_found: bool
    zone_id: int | None
    zone_role: Role | None
    zone_origin_role: Role | None
    zone_center: float | None
    zone_lower: float | None
    zone_upper: float | None
    zone_age_hours: float | None
    entry_distance_atr: float | None
    entry_inside_zone: bool
    prior_retests: int | None
    current_test_ordinal: int | None
    hours_since_last_retest: float | None
    source_pivots: int | None
    support_pivots: int | None
    resistance_pivots: int | None
    role_flips: int | None
    role_reversal: bool
    false_breaks: int | None
    previous_rejection_atr: float | None
    near_zone_fraction_2h: float | None
    approach_slope_atr_per_bar: float | None
    approach_distance_range_atr: float | None
    p44_residual_15m_pct: float | None
    p44_residual_q1: bool | None


@dataclass(frozen=True, slots=True)
class FeatureThreshold:
    symbol: str
    feature: str
    sample: int
    q25: float
    q50: float
    q75: float


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    family: str
    predicate: Callable[[ZoneFeatureRow], bool]


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_direction(value: str) -> Direction:
    if value not in {"Long", "Short"}:
        raise ValueError(f"unsupported direction: {value!r}")
    return cast(Direction, value)


def _parse_outcome(value: str) -> Outcome:
    if value not in {"favorable_first", "adverse_first", "neither"}:
        raise ValueError(f"unsupported outcome: {value!r}")
    return cast(Outcome, value)


def validation_root(root: Path, symbol: str, start: datetime, end: datetime) -> Path:
    suffix = f"{start:%Y%m%d}_{end:%Y%m%d}"
    return root / "reports" / "cross_asset_validation" / f"{symbol}_{suffix}"


def _metadata_matches_period(path: Path, *, start: datetime, end: datetime) -> bool:
    if not path.is_file():
        return False
    try:
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return False
    return (
        str(payload.get("evaluation_start", "")) == start.isoformat()
        and str(payload.get("evaluation_end", "")) == end.isoformat()
    )


def _dataset_dir_from_comparison(
    path: Path,
    *,
    root: Path,
    start: datetime,
    end: datetime,
) -> Path | None:
    if not _metadata_matches_period(path, start=start, end=end):
        return None
    try:
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    value = payload.get("dataset_dir")
    if not isinstance(value, str) or not value.strip():
        return None
    dataset_dir = Path(value.strip())
    if not dataset_dir.is_absolute():
        dataset_dir = root / dataset_dir
    return dataset_dir


def resolve_frozen_dataset_dir(
    root: Path,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> tuple[Path, str]:
    asset_root = validation_root(root, symbol, start, end)
    primary = asset_root / "p30" / "dataset"
    if (primary / "trade_15m.csv").is_file():
        return primary, "cross_asset_p30"

    comparison = asset_root / "p30" / "comparison.json"
    dataset_dir = _dataset_dir_from_comparison(
        comparison,
        root=root,
        start=start,
        end=end,
    )
    if dataset_dir is not None and (dataset_dir / "trade_15m.csv").is_file():
        return dataset_dir, "p30_comparison_dataset_dir"

    provenance_path = asset_root / "legacy_materialization.json"
    if provenance_path.is_file():
        try:
            provenance = cast(
                dict[str, Any],
                json.loads(provenance_path.read_text(encoding="utf-8-sig")),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            provenance = {}
        sources = provenance.get("sources")
        if isinstance(sources, dict):
            p30_source = sources.get("p30")
            if isinstance(p30_source, str) and p30_source:
                source_dir = Path(p30_source)
                if not source_dir.is_absolute():
                    source_dir = root / source_dir
                dataset_dir = _dataset_dir_from_comparison(
                    source_dir / "comparison.json",
                    root=root,
                    start=start,
                    end=end,
                )
                if dataset_dir is not None and (dataset_dir / "trade_15m.csv").is_file():
                    return dataset_dir, "legacy_materialization_p30_report"

    legacy_base = root / "reports" / "entry_research_v3"
    if legacy_base.is_dir():
        comparisons = sorted(
            legacy_base.glob(f"{symbol}_*/comparison.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for candidate in comparisons:
            dataset_dir = _dataset_dir_from_comparison(
                candidate,
                root=root,
                start=start,
                end=end,
            )
            if dataset_dir is not None and (dataset_dir / "trade_15m.csv").is_file():
                return dataset_dir, "legacy_v3_comparison_search"

    return primary, "missing"


def load_candles(path: Path) -> tuple[Candle, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"candle file not found: {path}")
    rows: list[Candle] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"opened_at", "closed_at", "open", "high", "low", "close", "volume"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"unexpected candle columns: {path}")
        for raw in reader:
            rows.append(
                Candle(
                    opened_at=parse_datetime(raw["opened_at"]),
                    closed_at=parse_datetime(raw["closed_at"]),
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                    volume=float(raw["volume"]),
                )
            )
    rows.sort(key=lambda item: item.closed_at)
    if len(rows) < ATR_PERIOD + PIVOT_SPAN * 2 + 10:
        raise ValueError(f"too few 15m candles: {len(rows)} in {path}")
    return tuple(rows)


def load_core_signals(
    path: Path,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> tuple[CoreSignal, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"P40 core signal file not found: {path}")
    rows: list[CoreSignal] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "symbol",
            "direction",
            "touch_at",
            "entry_price",
            "first_0_5_vs_1_0",
            "first_1_0_vs_1_0",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"unexpected P40 columns: {path}")
        for raw in reader:
            if raw["symbol"] != symbol:
                continue
            touch_at = parse_datetime(raw["touch_at"])
            if not (start <= touch_at < end):
                continue
            rows.append(
                CoreSignal(
                    symbol=symbol,
                    direction=_parse_direction(raw["direction"]),
                    touch_at=touch_at,
                    entry_price=float(raw["entry_price"]),
                    outcome_05=_parse_outcome(raw["first_0_5_vs_1_0"]),
                    outcome_10=_parse_outcome(raw["first_1_0_vs_1_0"]),
                )
            )
    rows.sort(key=lambda item: item.touch_at)
    if not rows:
        raise ValueError(f"no core signals for {symbol}: {path}")
    return tuple(rows)


def _true_range(current: Candle, previous_close: float | None) -> float:
    if previous_close is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous_close),
        abs(current.low - previous_close),
    )


def wilder_atr(candles: Sequence[Candle], period: int = ATR_PERIOD) -> tuple[float | None, ...]:
    if period <= 1:
        raise ValueError("ATR period must be greater than one")
    result: list[float | None] = [None] * len(candles)
    ranges: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        ranges.append(_true_range(candle, previous_close))
        previous_close = candle.close
    if len(ranges) < period:
        return tuple(result)
    first = statistics.fmean(ranges[:period])
    result[period - 1] = first
    current = first
    for index in range(period, len(ranges)):
        current = ((period - 1) * current + ranges[index]) / period
        result[index] = current
    return tuple(result)


def _is_pivot_high(candles: Sequence[Candle], index: int, span: int) -> bool:
    if index - span < 0 or index + span >= len(candles):
        return False
    center = candles[index].high
    others = [
        candles[pos].high
        for pos in range(index - span, index + span + 1)
        if pos != index
    ]
    return center > max(others)


def _is_pivot_low(candles: Sequence[Candle], index: int, span: int) -> bool:
    if index - span < 0 or index + span >= len(candles):
        return False
    center = candles[index].low
    others = [
        candles[pos].low
        for pos in range(index - span, index + span + 1)
        if pos != index
    ]
    return center < min(others)


def _distance_to_band(price: float, zone: Zone) -> float:
    if zone.lower <= price <= zone.upper:
        return 0.0
    return min(abs(price - zone.lower), abs(price - zone.upper))


def _linear_slope(values: Sequence[float]) -> float | None:
    if len(values) < 3:
        return None
    x_mean = (len(values) - 1) / 2.0
    y_mean = statistics.fmean(values)
    denominator = sum((index - x_mean) ** 2 for index in range(len(values)))
    if denominator <= 0:
        return None
    numerator = sum(
        (index - x_mean) * (value - y_mean)
        for index, value in enumerate(values)
    )
    return numerator / denominator


class ZoneDetector:
    def __init__(
        self,
        symbol: str,
        candles: tuple[Candle, ...],
        *,
        pivot_span: int = PIVOT_SPAN,
        atr_period: int = ATR_PERIOD,
        half_width_atr: float = ZONE_HALF_WIDTH_ATR,
        rearm_distance_atr: float = REARM_DISTANCE_ATR,
        break_confirm_closes: int = BREAK_CONFIRM_CLOSES,
    ) -> None:
        if pivot_span < 1:
            raise ValueError("pivot_span must be positive")
        if half_width_atr <= 0 or rearm_distance_atr <= 0:
            raise ValueError("zone width and rearm distance must be positive")
        if break_confirm_closes < 2:
            raise ValueError("break_confirm_closes must be at least two")
        self.symbol = symbol
        self.candles = candles
        self.atr = wilder_atr(candles, atr_period)
        self.pivot_span = pivot_span
        self.half_width_atr = half_width_atr
        self.rearm_distance_atr = rearm_distance_atr
        self.break_confirm_closes = break_confirm_closes
        self.zones: list[Zone] = []
        self.touch_events: list[TouchEvent] = []
        self.processed_index = -1
        self._next_zone_id = 1

    def _merge_or_create(
        self,
        *,
        price: float,
        half_width: float,
        role: Role,
        origin_at: datetime,
        confirmed_at: datetime,
    ) -> None:
        candidates = [
            zone
            for zone in self.zones
            if abs(zone.center - price) <= zone.half_width + half_width
        ]
        if candidates:
            zone = min(candidates, key=lambda item: abs(item.center - price))
            count = zone.source_pivots
            zone.center = (zone.center * count + price) / (count + 1)
            zone.half_width = (zone.half_width * count + half_width) / (count + 1)
            zone.source_pivots += 1
            if role == "support":
                zone.support_pivots += 1
            else:
                zone.resistance_pivots += 1
            return
        self.zones.append(
            Zone(
                zone_id=self._next_zone_id,
                center=price,
                half_width=half_width,
                origin_at=origin_at,
                confirmed_at=confirmed_at,
                origin_role=role,
                role=role,
                source_pivots=1,
                support_pivots=1 if role == "support" else 0,
                resistance_pivots=1 if role == "resistance" else 0,
            )
        )
        self._next_zone_id += 1

    def _record_touch(self, zone: Zone, candle: Candle) -> None:
        zone.retest_count += 1
        zone.last_retest_at = candle.closed_at
        zone.armed_for_retest = False
        zone.rejection_max_atr = 0.0
        self.touch_events.append(
            TouchEvent(
                symbol=self.symbol,
                zone_id=zone.zone_id,
                role=zone.role,
                event_at=candle.closed_at,
                retest_ordinal=zone.retest_count,
                source_pivots=zone.source_pivots,
                role_flips=zone.role_flips,
                false_breaks=zone.false_breaks,
                center=zone.center,
                lower=zone.lower,
                upper=zone.upper,
            )
        )

    def _update_zone(self, zone: Zone, candle: Candle, atr: float) -> None:
        intersects = candle.low <= zone.upper and candle.high >= zone.lower
        recorded_touch = intersects and zone.armed_for_retest
        if recorded_touch:
            self._record_touch(zone, candle)

        if not zone.armed_for_retest and not recorded_touch:
            if zone.role == "support":
                excursion = max(0.0, (candle.close - zone.upper) / atr)
                zone.rejection_max_atr = max(zone.rejection_max_atr, excursion)
                if candle.close >= zone.upper + self.rearm_distance_atr * atr:
                    zone.armed_for_retest = True
            else:
                excursion = max(0.0, (zone.lower - candle.close) / atr)
                zone.rejection_max_atr = max(zone.rejection_max_atr, excursion)
                if candle.close <= zone.lower - self.rearm_distance_atr * atr:
                    zone.armed_for_retest = True

        beyond = candle.close < zone.lower if zone.role == "support" else candle.close > zone.upper
        if beyond:
            zone.pending_break_closes += 1
            if zone.pending_break_closes >= self.break_confirm_closes:
                zone.role = "resistance" if zone.role == "support" else "support"
                zone.role_flips += 1
                zone.last_role_flip_at = candle.closed_at
                zone.pending_break_closes = 0
                zone.armed_for_retest = True
                zone.rejection_max_atr = 0.0
        elif zone.pending_break_closes:
            zone.false_breaks += 1
            zone.pending_break_closes = 0

    def _confirm_pivots(self, current_index: int) -> None:
        pivot_index = current_index - self.pivot_span
        if pivot_index < self.pivot_span:
            return
        pivot_atr = self.atr[pivot_index]
        if pivot_atr is None or pivot_atr <= 0:
            return
        pivot = self.candles[pivot_index]
        confirmed_at = self.candles[current_index].closed_at
        half_width = self.half_width_atr * pivot_atr
        if _is_pivot_low(self.candles, pivot_index, self.pivot_span):
            self._merge_or_create(
                price=pivot.low,
                half_width=half_width,
                role="support",
                origin_at=pivot.closed_at,
                confirmed_at=confirmed_at,
            )
        if _is_pivot_high(self.candles, pivot_index, self.pivot_span):
            self._merge_or_create(
                price=pivot.high,
                half_width=half_width,
                role="resistance",
                origin_at=pivot.closed_at,
                confirmed_at=confirmed_at,
            )

    def process_one(self, index: int) -> None:
        if index != self.processed_index + 1:
            raise ValueError("candles must be processed sequentially")
        candle = self.candles[index]
        current_atr = self.atr[index]
        if current_atr is not None and current_atr > 0:
            for zone in self.zones:
                self._update_zone(zone, candle, current_atr)
        self._confirm_pivots(index)
        self.processed_index = index

    def advance_until(self, timestamp: datetime) -> None:
        while (
            self.processed_index + 1 < len(self.candles)
            and self.candles[self.processed_index + 1].closed_at < timestamp
        ):
            self.process_one(self.processed_index + 1)

    def process_through(self, timestamp: datetime) -> None:
        while (
            self.processed_index + 1 < len(self.candles)
            and self.candles[self.processed_index + 1].closed_at <= timestamp
        ):
            self.process_one(self.processed_index + 1)

    def current_atr(self) -> float | None:
        if self.processed_index < 0:
            return None
        return self.atr[self.processed_index]

    def nearest_aligned_zone(self, direction: Direction, entry_price: float) -> Zone | None:
        role: Role = "support" if direction == "Long" else "resistance"
        candidates = [zone for zone in self.zones if zone.role == role]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda zone: (
                _distance_to_band(entry_price, zone),
                -zone.source_pivots,
                -zone.retest_count,
                zone.zone_id,
            ),
        )

    def approach_features(self, zone: Zone) -> tuple[float | None, float | None, float | None]:
        atr = self.current_atr()
        if atr is None or atr <= 0 or self.processed_index < 2:
            return None, None, None
        start = max(0, self.processed_index - APPROACH_BARS + 1)
        selected = self.candles[start : self.processed_index + 1]
        distances = [_distance_to_band(candle.close, zone) / atr for candle in selected]
        if not distances:
            return None, None, None
        near_fraction = sum(value <= 1.0 for value in distances) / len(distances)
        slope = _linear_slope(distances)
        distance_range = max(distances) - min(distances)
        return near_fraction, slope, distance_range


def segment_for(timestamp: datetime, start: datetime, calibration_days: int) -> Segment:
    elapsed = (timestamp - start).total_seconds() / 86400.0
    if elapsed < calibration_days:
        return "S1"
    if elapsed < calibration_days * 2:
        return "S2"
    return "S3"


def _load_p44_context(
    root: Path,
    *,
    start: datetime,
    end: datetime,
) -> tuple[dict[tuple[str, str, str], float], dict[str, float]]:
    base = (
        root
        / "reports"
        / "market_regime_p44_full_panel"
        / f"ENTRY_V1_{start:%Y%m%d}_{end:%Y%m%d}"
    )
    feature_path = base / "regime_features.csv"
    threshold_path = base / "calibration_thresholds.csv"
    if not feature_path.is_file() or not threshold_path.is_file():
        return {}, {}
    values: dict[tuple[str, str, str], float] = {}
    with feature_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {
            "symbol",
            "direction",
            "touch_at",
            "directional_alt_btc_residual_15m_pct",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            return {}, {}
        for raw in reader:
            value = raw["directional_alt_btc_residual_15m_pct"].strip()
            if not value:
                continue
            key = (raw["symbol"], raw["direction"], parse_datetime(raw["touch_at"]).isoformat())
            values[key] = float(value)
    q25: dict[str, float] = {}
    with threshold_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "feature", "q25"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            return values, {}
        for raw in reader:
            if raw["feature"] == "directional_alt_btc_residual_15m_pct":
                q25[raw["symbol"]] = float(raw["q25"])
    return values, q25


def build_feature_row(
    signal: CoreSignal,
    *,
    detector: ZoneDetector,
    start: datetime,
    calibration_days: int,
    p44_values: dict[tuple[str, str, str], float],
    p44_q25: dict[str, float],
) -> ZoneFeatureRow:
    detector.advance_until(signal.touch_at)
    atr = detector.current_atr()
    zone = detector.nearest_aligned_zone(signal.direction, signal.entry_price)
    p44_value = p44_values.get(
        (signal.symbol, signal.direction, signal.touch_at.isoformat())
    )
    p44_q1: bool | None = None
    threshold = p44_q25.get(signal.symbol)
    if p44_value is not None and threshold is not None:
        p44_q1 = p44_value <= threshold

    if zone is None or atr is None or atr <= 0:
        return ZoneFeatureRow(
            symbol=signal.symbol,
            display_symbol=DISPLAY_SYMBOLS.get(signal.symbol, signal.symbol.replace("USDT", "")),
            direction=signal.direction,
            touch_at=signal.touch_at,
            segment=segment_for(signal.touch_at, start, calibration_days),
            entry_price=signal.entry_price,
            outcome_05=signal.outcome_05,
            outcome_10=signal.outcome_10,
            zone_found=False,
            zone_id=None,
            zone_role=None,
            zone_origin_role=None,
            zone_center=None,
            zone_lower=None,
            zone_upper=None,
            zone_age_hours=None,
            entry_distance_atr=None,
            entry_inside_zone=False,
            prior_retests=None,
            current_test_ordinal=None,
            hours_since_last_retest=None,
            source_pivots=None,
            support_pivots=None,
            resistance_pivots=None,
            role_flips=None,
            role_reversal=False,
            false_breaks=None,
            previous_rejection_atr=None,
            near_zone_fraction_2h=None,
            approach_slope_atr_per_bar=None,
            approach_distance_range_atr=None,
            p44_residual_15m_pct=p44_value,
            p44_residual_q1=p44_q1,
        )

    distance_atr = _distance_to_band(signal.entry_price, zone) / atr
    near_fraction, slope, distance_range = detector.approach_features(zone)
    hours_since_last: float | None = None
    if zone.last_retest_at is not None:
        hours_since_last = (signal.touch_at - zone.last_retest_at).total_seconds() / 3600.0
    return ZoneFeatureRow(
        symbol=signal.symbol,
        display_symbol=DISPLAY_SYMBOLS.get(signal.symbol, signal.symbol.replace("USDT", "")),
        direction=signal.direction,
        touch_at=signal.touch_at,
        segment=segment_for(signal.touch_at, start, calibration_days),
        entry_price=signal.entry_price,
        outcome_05=signal.outcome_05,
        outcome_10=signal.outcome_10,
        zone_found=True,
        zone_id=zone.zone_id,
        zone_role=zone.role,
        zone_origin_role=zone.origin_role,
        zone_center=zone.center,
        zone_lower=zone.lower,
        zone_upper=zone.upper,
        zone_age_hours=(signal.touch_at - zone.confirmed_at).total_seconds() / 3600.0,
        entry_distance_atr=distance_atr,
        entry_inside_zone=distance_atr == 0.0,
        prior_retests=zone.retest_count,
        current_test_ordinal=zone.retest_count + 1,
        hours_since_last_retest=hours_since_last,
        source_pivots=zone.source_pivots,
        support_pivots=zone.support_pivots,
        resistance_pivots=zone.resistance_pivots,
        role_flips=zone.role_flips,
        role_reversal=zone.role_flips > 0,
        false_breaks=zone.false_breaks,
        previous_rejection_atr=(
            zone.rejection_max_atr if zone.last_retest_at is not None else None
        ),
        near_zone_fraction_2h=near_fraction,
        approach_slope_atr_per_bar=slope,
        approach_distance_range_atr=distance_range,
        p44_residual_15m_pct=p44_value,
        p44_residual_q1=p44_q1,
    )


def _near(row: ZoneFeatureRow, distance: float) -> bool:
    return (
        row.zone_found
        and row.entry_distance_atr is not None
        and row.entry_distance_atr <= distance
    )


def frozen_rules() -> tuple[Rule, ...]:
    return (
        Rule("baseline", "baseline", lambda row: True),
        Rule("inside_aligned_zone", "distance", lambda row: row.entry_inside_zone),
        Rule("near_aligned_0_25atr", "distance", lambda row: _near(row, 0.25)),
        Rule("near_aligned_0_50atr", "distance", lambda row: _near(row, 0.50)),
        Rule("near_aligned_1_00atr", "distance", lambda row: _near(row, 1.00)),
        Rule(
            "near_0_50_second_plus",
            "touch_count",
            lambda row: _near(row, 0.50) and (row.current_test_ordinal or 0) >= 2,
        ),
        Rule(
            "near_0_50_third_plus",
            "touch_count",
            lambda row: _near(row, 0.50) and (row.current_test_ordinal or 0) >= 3,
        ),
        Rule(
            "near_0_50_fourth_plus",
            "touch_count",
            lambda row: _near(row, 0.50) and (row.current_test_ordinal or 0) >= 4,
        ),
        Rule(
            "near_0_50_multi_pivot",
            "structure",
            lambda row: _near(row, 0.50) and (row.source_pivots or 0) >= 2,
        ),
        Rule(
            "near_0_50_role_reversal",
            "structure",
            lambda row: _near(row, 0.50) and row.role_reversal,
        ),
        Rule(
            "near_0_50_false_break_history",
            "structure",
            lambda row: _near(row, 0.50) and (row.false_breaks or 0) >= 1,
        ),
        Rule(
            "near_0_50_fresh_lt24h",
            "age",
            lambda row: _near(row, 0.50)
            and row.zone_age_hours is not None
            and row.zone_age_hours < 24.0,
        ),
        Rule(
            "near_0_50_old_ge7d",
            "age",
            lambda row: _near(row, 0.50)
            and row.zone_age_hours is not None
            and row.zone_age_hours >= 168.0,
        ),
        Rule(
            "near_0_50_prev_rejection_ge2atr",
            "rejection",
            lambda row: _near(row, 0.50)
            and row.previous_rejection_atr is not None
            and row.previous_rejection_atr >= 2.0,
        ),
        Rule(
            "near_0_50_pressure_dense",
            "compression",
            lambda row: _near(row, 0.50)
            and row.near_zone_fraction_2h is not None
            and row.near_zone_fraction_2h >= 0.75
            and row.approach_slope_atr_per_bar is not None
            and row.approach_slope_atr_per_bar < 0.0,
        ),
        Rule(
            "p44_residual_q1",
            "p44_exploratory",
            lambda row: row.p44_residual_q1 is True,
        ),
        Rule(
            "p44_q1_and_near_0_50",
            "p44_exploratory",
            lambda row: row.p44_residual_q1 is True and _near(row, 0.50),
        ),
        Rule(
            "p44_q1_and_second_plus",
            "p44_exploratory",
            lambda row: row.p44_residual_q1 is True
            and _near(row, 0.50)
            and (row.current_test_ordinal or 0) >= 2,
        ),
        Rule(
            "p44_q1_and_role_reversal",
            "p44_exploratory",
            lambda row: row.p44_residual_q1 is True
            and _near(row, 0.50)
            and row.role_reversal,
        ),
    )


def _outcome_metrics(rows: Sequence[ZoneFeatureRow]) -> dict[str, float | int | None]:
    count = len(rows)
    favorable05 = sum(row.outcome_05 == "favorable_first" for row in rows)
    adverse05 = sum(row.outcome_05 == "adverse_first" for row in rows)
    favorable10 = sum(row.outcome_10 == "favorable_first" for row in rows)
    adverse10 = sum(row.outcome_10 == "adverse_first" for row in rows)
    decisive05 = favorable05 + adverse05
    decisive10 = favorable10 + adverse10
    return {
        "sample": count,
        "favorable_05": favorable05,
        "adverse_05": adverse05,
        "neither_05": count - favorable05 - adverse05,
        "win_05_all_pct": None if count == 0 else 100.0 * favorable05 / count,
        "win_05_decisive_pct": None if decisive05 == 0 else 100.0 * favorable05 / decisive05,
        "favorable_10": favorable10,
        "adverse_10": adverse10,
        "neither_10": count - favorable10 - adverse10,
        "win_10_all_pct": None if count == 0 else 100.0 * favorable10 / count,
        "win_10_decisive_pct": None if decisive10 == 0 else 100.0 * favorable10 / decisive10,
    }


def _uplift(value: float | int | None, baseline: float | int | None) -> float | None:
    if not isinstance(value, (int, float)) or not isinstance(baseline, (int, float)):
        return None
    return float(value) - float(baseline)


def build_rule_matrix(
    rows: Sequence[ZoneFeatureRow],
    *,
    symbols: Sequence[str],
) -> list[dict[str, Any]]:
    rules = frozen_rules()
    result: list[dict[str, Any]] = []
    for symbol in symbols:
        asset_rows = [row for row in rows if row.symbol == symbol]
        baseline = _outcome_metrics(asset_rows)
        for rule in rules:
            selected = [row for row in asset_rows if rule.predicate(row)]
            metrics = _outcome_metrics(selected)
            result.append(
                {
                    "symbol": symbol,
                    "rule": rule.name,
                    "family": rule.family,
                    **metrics,
                    "uplift_05_all_pp": _uplift(
                        metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                    ),
                    "uplift_10_all_pp": _uplift(
                        metrics["win_10_all_pct"], baseline["win_10_all_pct"]
                    ),
                }
            )
    return result


def build_rule_transfer(matrix: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_rule: dict[str, list[dict[str, Any]]] = {}
    for row in matrix:
        by_rule.setdefault(str(row["rule"]), []).append(row)
    result: list[dict[str, Any]] = []
    for rule_name, items in by_rule.items():
        family = str(items[0]["family"])
        valid = [
            cast(float, item["uplift_05_all_pp"])
            for item in items
            if isinstance(item.get("uplift_05_all_pp"), (int, float))
            and int(item.get("sample", 0)) > 0
        ]
        samples = [int(item.get("sample", 0)) for item in items]
        result.append(
            {
                "rule": rule_name,
                "family": family,
                "assets_with_sample": len(valid),
                "assets_improved": sum(value > 0 for value in valid),
                "assets_worsened": sum(value < 0 for value in valid),
                "assets_equal": sum(value == 0 for value in valid),
                "median_uplift_05_all_pp": None if not valid else statistics.median(valid),
                "min_uplift_05_all_pp": None if not valid else min(valid),
                "max_uplift_05_all_pp": None if not valid else max(valid),
                "median_sample": None if not samples else statistics.median(samples),
                "total_sample": sum(samples),
            }
        )
    result.sort(key=lambda item: (str(item["family"]), str(item["rule"])))
    return result


def build_segment_matrix(rows: Sequence[ZoneFeatureRow]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol in sorted({row.symbol for row in rows}):
        for segment in ("S1", "S2", "S3"):
            asset_segment = [
                row for row in rows if row.symbol == symbol and row.segment == segment
            ]
            baseline = _outcome_metrics(asset_segment)
            for rule in frozen_rules():
                selected = [row for row in asset_segment if rule.predicate(row)]
                metrics = _outcome_metrics(selected)
                result.append(
                    {
                        "symbol": symbol,
                        "segment": segment,
                        "rule": rule.name,
                        "family": rule.family,
                        **metrics,
                        "uplift_05_all_pp": _uplift(
                            metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                        ),
                    }
                )
    return result


def build_direction_matrix(rows: Sequence[ZoneFeatureRow]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol in sorted({row.symbol for row in rows}):
        for direction in ("Long", "Short"):
            asset_direction = [
                row for row in rows if row.symbol == symbol and row.direction == direction
            ]
            baseline = _outcome_metrics(asset_direction)
            for rule in frozen_rules():
                selected = [row for row in asset_direction if rule.predicate(row)]
                metrics = _outcome_metrics(selected)
                result.append(
                    {
                        "symbol": symbol,
                        "direction": direction,
                        "rule": rule.name,
                        "family": rule.family,
                        **metrics,
                        "uplift_05_all_pp": _uplift(
                            metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                        ),
                    }
                )
    return result


def _ordinal_bucket(row: ZoneFeatureRow) -> str:
    ordinal = row.current_test_ordinal
    if ordinal is None:
        return "no_zone"
    if ordinal <= 1:
        return "first"
    if ordinal == 2:
        return "second"
    if ordinal == 3:
        return "third"
    return "fourth_plus"


def build_touch_ordinal_matrix(rows: Sequence[ZoneFeatureRow]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for symbol in sorted({row.symbol for row in rows}):
        baseline_rows = [row for row in rows if row.symbol == symbol]
        baseline = _outcome_metrics(baseline_rows)
        near_rows = [row for row in baseline_rows if _near(row, NEAR_ZONE_ATR)]
        for bucket in ("first", "second", "third", "fourth_plus"):
            selected = [row for row in near_rows if _ordinal_bucket(row) == bucket]
            metrics = _outcome_metrics(selected)
            result.append(
                {
                    "symbol": symbol,
                    "touch_bucket": bucket,
                    **metrics,
                    "uplift_vs_asset_baseline_05_all_pp": _uplift(
                        metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                    ),
                }
            )
    return result


QUARTILE_FEATURES: tuple[str, ...] = (
    "zone_age_hours",
    "entry_distance_atr",
    "hours_since_last_retest",
    "previous_rejection_atr",
    "near_zone_fraction_2h",
    "approach_slope_atr_per_bar",
    "approach_distance_range_atr",
)


def _feature_value(row: ZoneFeatureRow, feature: str) -> float | None:
    value = getattr(row, feature)
    return float(value) if isinstance(value, (int, float)) else None


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute quantile of empty sample")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def build_thresholds(rows: Sequence[ZoneFeatureRow]) -> list[FeatureThreshold]:
    result: list[FeatureThreshold] = []
    for symbol in sorted({row.symbol for row in rows}):
        s1 = [row for row in rows if row.symbol == symbol and row.segment == "S1"]
        for feature in QUARTILE_FEATURES:
            values = sorted(
                value
                for row in s1
                if _near(row, NEAR_ZONE_ATR)
                for value in [_feature_value(row, feature)]
                if value is not None and math.isfinite(value)
            )
            if len(values) < 8:
                continue
            result.append(
                FeatureThreshold(
                    symbol=symbol,
                    feature=feature,
                    sample=len(values),
                    q25=_quantile(values, 0.25),
                    q50=_quantile(values, 0.50),
                    q75=_quantile(values, 0.75),
                )
            )
    return result


def classify_quartile(value: float, threshold: FeatureThreshold) -> Quartile:
    if value <= threshold.q25:
        return "Q1"
    if value <= threshold.q50:
        return "Q2"
    if value <= threshold.q75:
        return "Q3"
    return "Q4"


def build_feature_quartile_oos(
    rows: Sequence[ZoneFeatureRow], thresholds: Sequence[FeatureThreshold]
) -> list[dict[str, Any]]:
    lookup = {(item.symbol, item.feature): item for item in thresholds}
    result: list[dict[str, Any]] = []
    for symbol in sorted({row.symbol for row in rows}):
        oos = [
            row
            for row in rows
            if row.symbol == symbol and row.segment in {"S2", "S3"} and _near(row, NEAR_ZONE_ATR)
        ]
        baseline = _outcome_metrics(oos)
        for feature in QUARTILE_FEATURES:
            threshold = lookup.get((symbol, feature))
            if threshold is None:
                continue
            for quartile in ("Q1", "Q2", "Q3", "Q4"):
                selected = [
                    row
                    for row in oos
                    if (value := _feature_value(row, feature)) is not None
                    and classify_quartile(value, threshold) == quartile
                ]
                metrics = _outcome_metrics(selected)
                result.append(
                    {
                        "symbol": symbol,
                        "feature": feature,
                        "quartile": quartile,
                        **metrics,
                        "uplift_vs_near_zone_oos_05_all_pp": _uplift(
                            metrics["win_05_all_pct"], baseline["win_05_all_pct"]
                        ),
                    }
                )
    return result


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_dataclass_csv(path: Path, rows: Sequence[Any]) -> None:
    _write_csv(path, [asdict(row) for row in rows])


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zone_catalog_rows(symbol: str, zones: Sequence[Zone]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": symbol,
            "zone_id": zone.zone_id,
            "center": zone.center,
            "lower": zone.lower,
            "upper": zone.upper,
            "origin_at": zone.origin_at.isoformat(),
            "confirmed_at": zone.confirmed_at.isoformat(),
            "origin_role": zone.origin_role,
            "final_role": zone.role,
            "source_pivots": zone.source_pivots,
            "support_pivots": zone.support_pivots,
            "resistance_pivots": zone.resistance_pivots,
            "retest_count": zone.retest_count,
            "last_retest_at": (
                None if zone.last_retest_at is None else zone.last_retest_at.isoformat()
            ),
            "role_flips": zone.role_flips,
            "last_role_flip_at": None
            if zone.last_role_flip_at is None
            else zone.last_role_flip_at.isoformat(),
            "false_breaks": zone.false_breaks,
        }
        for zone in zones
    ]


def _summary_markdown(
    *,
    rows: Sequence[ZoneFeatureRow],
    transfer: Sequence[dict[str, Any]],
    p44_available: bool,
) -> str:
    lines = [
        "# P45 Multi-Touch Support/Resistance Zones",
        "",
        "Исследование использует только frozen локальные данные. Live trading logic не изменена.",
        "",
        f"Core signals analysed: **{len(rows)}**.",
        f"P44 residual context joined: **{'yes' if p44_available else 'no'}**.",
        "",
        "## Frozen zone semantics",
        "",
        f"- 15m confirmed pivots: left/right span = {PIVOT_SPAN} bars.",
        f"- Zone half-width = {ZONE_HALF_WIDTH_ATR:.2f} ATR({ATR_PERIOD}).",
        f"- Independent retest rearms only after {REARM_DISTANCE_ATR:.2f} ATR excursion.",
        (
            f"- Role flip requires {BREAK_CONFIRM_CLOSES} consecutive closes "
            "through the far zone edge."
        ),
        "- At each exact Entry touch, only 15m candles already closed before touch_at are visible.",
        "",
        "## Cross-asset transfer (discovery; not a live rule)",
        "",
        "| Rule | Assets improved | worsened | median uplift +0.5/-1 | total sample |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in transfer:
        if item["rule"] == "baseline":
            continue
        uplift = item["median_uplift_05_all_pp"]
        uplift_text = "n/a" if uplift is None else f"{float(uplift):+.2f} pp"
        lines.append(
            f"| {item['rule']} | {item['assets_improved']} | {item['assets_worsened']} | "
            f"{uplift_text} | {item['total_sample']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation guardrails",
            "",
            (
                "- P45 is still discovery on the same frozen 90-day interval; "
                "it may generate candidates, not confirmation."
            ),
            (
                "- Touch-count, role-reversal and compression results must be checked "
                "across assets and S1/S2/S3."
            ),
            (
                "- P44 residual interactions are explicitly exploratory because that "
                "feature was discovered after P44 OOS review."
            ),
            "- Any rule promoted from P45 must be frozen before a new temporal OOS holdout.",
            "",
        ]
    )
    return "\n".join(lines)


def _zip_report(output_dir: Path) -> Path:
    zip_path = output_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir.parent))
    return zip_path


def run_analysis(
    *,
    root: Path,
    start: datetime,
    end: datetime,
    symbols: Sequence[str] = DEFAULT_SYMBOLS,
    calibration_days: int = 30,
    force: bool = False,
) -> Path:
    if end <= start:
        raise ValueError("end must be after start")
    if calibration_days <= 0:
        raise ValueError("calibration_days must be positive")
    output_dir = (
        root
        / "reports"
        / "multi_touch_sr_p45"
        / f"ENTRY_V1_{start:%Y%m%d}_{end:%Y%m%d}"
    )
    if output_dir.exists() and not force:
        raise FileExistsError(f"P45 output already exists; rerun with --force: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print("P45 PRECHECK - frozen local 15m + P40 only; network is not used")
    source_manifest: list[dict[str, Any]] = []
    dataset_dirs: dict[str, Path] = {}
    signal_paths: dict[str, Path] = {}
    for symbol in symbols:
        dataset_dir, source = resolve_frozen_dataset_dir(
            root,
            symbol=symbol,
            start=start,
            end=end,
        )
        candle_path = dataset_dir / "trade_15m.csv"
        signal_path = validation_root(root, symbol, start, end) / "p40" / "absorption_features.csv"
        if not candle_path.is_file():
            raise FileNotFoundError(
                f"missing frozen 15m data for {symbol}; resolved source={source}: {candle_path}"
            )
        if not signal_path.is_file():
            raise FileNotFoundError(f"missing completed P40 core for {symbol}: {signal_path}")
        dataset_dirs[symbol] = dataset_dir
        signal_paths[symbol] = signal_path
        source_manifest.append(
            {
                "symbol": symbol,
                "dataset_source": source,
                "trade_15m": str(candle_path),
                "trade_15m_sha256": _sha256_file(candle_path),
                "p40_core": str(signal_path),
                "p40_core_sha256": _sha256_file(signal_path),
            }
        )
        print(f"  OK {symbol}: 15m [{source}] + P40 core")

    p44_values, p44_q25 = _load_p44_context(root, start=start, end=end)
    p44_available = bool(p44_values and p44_q25)
    p44_status = "available" if p44_available else "not found; optional join skipped"
    print(f"P44 residual context: {p44_status}")

    feature_rows: list[ZoneFeatureRow] = []
    touch_events: list[TouchEvent] = []
    zone_catalog: list[dict[str, Any]] = []
    for index, symbol in enumerate(symbols, start=1):
        print(f"P45 ASSET {index}/{len(symbols)}: {symbol}")
        candles = load_candles(dataset_dirs[symbol] / "trade_15m.csv")
        signals = load_core_signals(
            signal_paths[symbol],
            symbol=symbol,
            start=start,
            end=end,
        )
        detector = ZoneDetector(symbol, candles)
        for signal_index, signal in enumerate(signals, start=1):
            feature_rows.append(
                build_feature_row(
                    signal,
                    detector=detector,
                    start=start,
                    calibration_days=calibration_days,
                    p44_values=p44_values,
                    p44_q25=p44_q25,
                )
            )
            if signal_index % 50 == 0 or signal_index == len(signals):
                print(
                    f"  signals {signal_index}/{len(signals)} zones={len(detector.zones)} "
                    f"retests={len(detector.touch_events)}"
                )
        detector.process_through(end)
        touch_events.extend(detector.touch_events)
        zone_catalog.extend(_zone_catalog_rows(symbol, detector.zones))

    feature_rows.sort(key=lambda row: (row.touch_at, row.symbol, row.direction))
    touch_events.sort(key=lambda row: (row.event_at, row.symbol, row.zone_id))
    rule_matrix = build_rule_matrix(feature_rows, symbols=symbols)
    transfer = build_rule_transfer(rule_matrix)
    segment_matrix = build_segment_matrix(feature_rows)
    direction_matrix = build_direction_matrix(feature_rows)
    ordinal_matrix = build_touch_ordinal_matrix(feature_rows)
    thresholds = build_thresholds(feature_rows)
    quartile_oos = build_feature_quartile_oos(feature_rows, thresholds)

    _write_dataclass_csv(output_dir / "core_zone_features.csv", feature_rows)
    _write_dataclass_csv(output_dir / "zone_touch_events_15m_proxy.csv", touch_events)
    _write_csv(output_dir / "zone_catalog_final.csv", zone_catalog)
    _write_csv(output_dir / "asset_rule_matrix.csv", rule_matrix)
    _write_csv(output_dir / "cross_asset_rule_transfer.csv", transfer)
    _write_csv(output_dir / "segment_rule_matrix.csv", segment_matrix)
    _write_csv(output_dir / "direction_rule_matrix.csv", direction_matrix)
    _write_csv(output_dir / "touch_ordinal_matrix.csv", ordinal_matrix)
    _write_dataclass_csv(output_dir / "s1_feature_thresholds.csv", thresholds)
    _write_csv(output_dir / "feature_quartiles_oos.csv", quartile_oos)
    _write_csv(output_dir / "source_manifest.csv", source_manifest)

    near_count = sum(_near(row, NEAR_ZONE_ATR) for row in feature_rows)
    summary = {
        "architecture": "p45_multi_touch_support_resistance_zones",
        "evaluation_start": start.isoformat(),
        "evaluation_end": end.isoformat(),
        "calibration_days": calibration_days,
        "symbols": list(symbols),
        "core_signals": len(feature_rows),
        "near_aligned_zone_0_5atr": near_count,
        "near_aligned_zone_0_5atr_pct": 100.0 * near_count / len(feature_rows),
        "zone_catalog_rows": len(zone_catalog),
        "zone_touch_events": len(touch_events),
        "p44_context_joined": p44_available,
        "frozen_parameters": {
            "pivot_span": PIVOT_SPAN,
            "atr_period": ATR_PERIOD,
            "zone_half_width_atr": ZONE_HALF_WIDTH_ATR,
            "rearm_distance_atr": REARM_DISTANCE_ATR,
            "break_confirm_closes": BREAK_CONFIRM_CLOSES,
            "approach_bars": APPROACH_BARS,
            "near_zone_atr": NEAR_ZONE_ATR,
        },
        "guardrails": [
            "Only candles closed strictly before exact touch_at are visible to signal features.",
            "P45 is discovery on the existing frozen 90-day interval, not confirmatory OOS.",
            (
                "P44 residual interactions are exploratory and cannot be promoted "
                "without a new holdout."
            ),
            "No live trading, risk, stop, take-profit, leverage, or execution logic is modified.",
            "No market data is downloaded.",
        ],
    }
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "summary.md").write_text(
        _summary_markdown(rows=feature_rows, transfer=transfer, p44_available=p44_available),
        encoding="utf-8",
    )
    _write_json(
        output_dir / "RUN_COMPLETE.json",
        {
            "complete": True,
            "core_signals": len(feature_rows),
            "zone_catalog_rows": len(zone_catalog),
            "zone_touch_events": len(touch_events),
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )
    zip_path = _zip_report(output_dir)
    print(f"P45 COMPLETE: core_features={len(feature_rows)} near_0.5ATR={near_count}")
    print(f"Summary: {output_dir / 'summary.md'}")
    print(f"Result ZIP: {zip_path}")
    return zip_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P45 multi-touch support/resistance research")
    parser.add_argument("--root", default="C:/cripta")
    parser.add_argument("--start", default="2026-05-18T00:00:00+00:00")
    parser.add_argument("--end", default="2026-08-16T00:00:00+00:00")
    parser.add_argument("--calibration-days", type=int, default=30)
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_analysis(
        root=Path(args.root),
        start=parse_datetime(args.start),
        end=parse_datetime(args.end),
        symbols=tuple(args.symbols),
        calibration_days=int(args.calibration_days),
        force=bool(args.force),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
