from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import statistics
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

Direction = Literal["Long", "Short"]
Outcome = Literal["favorable_first", "adverse_first", "neither"]
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
ALT_SYMBOLS: frozenset[str] = frozenset(
    {
        "UNIUSDT",
        "LINKUSDT",
        "XRPUSDT",
        "1000PEPEUSDT",
        "SOLUSDT",
        "DOGEUSDT",
        "ADAUSDT",
    }
)
DISPLAY_SYMBOLS: dict[str, str] = {"1000PEPEUSDT": "PEPE"}
FIVE_MINUTES = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class PriceSeries:
    symbol: str
    closed_at: tuple[datetime, ...]
    close: tuple[float, ...]
    return_by_time: dict[datetime, float]

    def index_strictly_before(self, timestamp: datetime) -> int:
        return bisect.bisect_left(self.closed_at, timestamp) - 1


@dataclass(frozen=True, slots=True)
class CoreSignal:
    symbol: str
    direction: Direction
    touch_at: datetime
    outcome_05: Outcome
    outcome_10: Outcome


@dataclass(frozen=True, slots=True)
class FeatureThreshold:
    symbol: str
    feature: str
    sample: int
    q25: float
    q50: float
    q75: float


@dataclass(frozen=True, slots=True)
class RegimeRow:
    symbol: str
    display_symbol: str
    direction: Direction
    touch_at: datetime
    segment: Segment
    outcome_05: Outcome
    outcome_10: Outcome
    directional_asset_15m_pct: float | None
    directional_asset_60m_pct: float | None
    directional_btc_5m_pct: float | None
    directional_btc_15m_pct: float | None
    directional_btc_60m_pct: float | None
    btc_5m_shock_z: float | None
    btc_volatility_3h_pct: float | None
    directional_eth_15m_pct: float | None
    directional_eth_60m_pct: float | None
    directional_eth_minus_btc_15m_pct: float | None
    alt_btc_corr_3h: float | None
    alt_btc_corr_12h: float | None
    alt_btc_beta_12h: float | None
    directional_alt_btc_residual_15m_pct: float | None
    directional_alt_btc_residual_60m_pct: float | None
    directional_panel_breadth_15m: float | None
    directional_panel_breadth_60m: float | None
    directional_panel_median_15m_pct: float | None
    directional_panel_median_60m_pct: float | None
    panel_dispersion_15m_pct: float | None
    directional_alt_breadth_15m: float | None
    directional_alt_breadth_60m: float | None
    directional_alt_median_15m_pct: float | None
    directional_alt_median_60m_pct: float | None


FeatureGetter = Callable[[RegimeRow], float | None]


def _attr(name: str) -> FeatureGetter:
    def getter(row: RegimeRow) -> float | None:
        value = getattr(row, name)
        return cast(float | None, value)

    return getter


FEATURES: dict[str, FeatureGetter] = {
    "directional_btc_5m_pct": _attr("directional_btc_5m_pct"),
    "directional_btc_15m_pct": _attr("directional_btc_15m_pct"),
    "directional_btc_60m_pct": _attr("directional_btc_60m_pct"),
    "btc_5m_shock_z": _attr("btc_5m_shock_z"),
    "btc_volatility_3h_pct": _attr("btc_volatility_3h_pct"),
    "directional_eth_15m_pct": _attr("directional_eth_15m_pct"),
    "directional_eth_60m_pct": _attr("directional_eth_60m_pct"),
    "directional_eth_minus_btc_15m_pct": _attr("directional_eth_minus_btc_15m_pct"),
    "alt_btc_corr_3h": _attr("alt_btc_corr_3h"),
    "alt_btc_corr_12h": _attr("alt_btc_corr_12h"),
    "alt_btc_beta_12h": _attr("alt_btc_beta_12h"),
    "directional_alt_btc_residual_15m_pct": _attr(
        "directional_alt_btc_residual_15m_pct"
    ),
    "directional_alt_btc_residual_60m_pct": _attr(
        "directional_alt_btc_residual_60m_pct"
    ),
    "directional_panel_breadth_15m": _attr("directional_panel_breadth_15m"),
    "directional_panel_breadth_60m": _attr("directional_panel_breadth_60m"),
    "directional_panel_median_15m_pct": _attr("directional_panel_median_15m_pct"),
    "directional_panel_median_60m_pct": _attr("directional_panel_median_60m_pct"),
    "panel_dispersion_15m_pct": _attr("panel_dispersion_15m_pct"),
    "directional_alt_breadth_15m": _attr("directional_alt_breadth_15m"),
    "directional_alt_breadth_60m": _attr("directional_alt_breadth_60m"),
    "directional_alt_median_15m_pct": _attr("directional_alt_median_15m_pct"),
    "directional_alt_median_60m_pct": _attr("directional_alt_median_60m_pct"),
}


@dataclass(frozen=True, slots=True)
class CandidateRule:
    name: str
    required_features: tuple[str, ...]
    predicate: Callable[[RegimeRow, dict[str, FeatureThreshold]], bool]


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


def load_price_series(path: Path, *, symbol: str) -> PriceSeries:
    if not path.is_file():
        raise FileNotFoundError(f"5m price dataset not found: {path}")
    points: list[tuple[datetime, float]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {"closed_at", "close"}.issubset(reader.fieldnames):
            raise ValueError(f"unexpected 5m dataset columns: {path}")
        for raw in reader:
            closed_at = parse_datetime(raw["closed_at"])
            close = float(raw["close"])
            if close > 0:
                points.append((closed_at, close))
    points.sort(key=lambda item: item[0])
    if len(points) < 20:
        raise ValueError(f"too few 5m price rows for {symbol}: {len(points)}")
    times = tuple(item[0] for item in points)
    closes = tuple(item[1] for item in points)
    returns: dict[datetime, float] = {}
    for index in range(1, len(points)):
        previous = closes[index - 1]
        current = closes[index]
        if previous > 0:
            returns[times[index]] = (current / previous - 1.0) * 100.0
    return PriceSeries(symbol=symbol, closed_at=times, close=closes, return_by_time=returns)


def load_core_signals(
    path: Path,
    *,
    symbol: str,
    start: datetime,
    end: datetime,
) -> tuple[CoreSignal, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"P40 core signal file not found: {path}")
    signals: list[CoreSignal] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"symbol", "direction", "touch_at", "first_0_5_vs_1_0", "first_1_0_vs_1_0"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"unexpected P40 columns: {path}")
        for raw in reader:
            if raw["symbol"] != symbol:
                continue
            touch_at = parse_datetime(raw["touch_at"])
            if not (start <= touch_at < end):
                continue
            signals.append(
                CoreSignal(
                    symbol=symbol,
                    direction=_parse_direction(raw["direction"]),
                    touch_at=touch_at,
                    outcome_05=_parse_outcome(raw["first_0_5_vs_1_0"]),
                    outcome_10=_parse_outcome(raw["first_1_0_vs_1_0"]),
                )
            )
    signals.sort(key=lambda item: item.touch_at)
    if not signals:
        raise ValueError(f"no frozen core signals for {symbol}: {path}")
    return tuple(signals)


def return_pct(series: PriceSeries, touch_at: datetime, bars: int) -> float | None:
    if bars <= 0:
        raise ValueError("bars must be positive")
    end_index = series.index_strictly_before(touch_at)
    start_index = end_index - bars
    if start_index < 0 or end_index < 0:
        return None
    start = series.close[start_index]
    end = series.close[end_index]
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def aligned_returns(
    left: PriceSeries,
    right: PriceSeries,
    touch_at: datetime,
    lookback: timedelta,
) -> tuple[list[float], list[float]]:
    left_end = left.index_strictly_before(touch_at)
    if left_end < 1:
        return [], []
    end_time = left.closed_at[left_end]
    start_time = end_time - lookback + FIVE_MINUTES
    start_index = bisect.bisect_left(left.closed_at, start_time)
    xs: list[float] = []
    ys: list[float] = []
    for timestamp in left.closed_at[start_index : left_end + 1]:
        left_return = left.return_by_time.get(timestamp)
        right_return = right.return_by_time.get(timestamp)
        if left_return is None or right_return is None:
            continue
        xs.append(left_return)
        ys.append(right_return)
    return xs, ys


def corr_beta(
    btc_returns: Sequence[float], alt_returns: Sequence[float]
) -> tuple[float | None, float | None]:
    count = min(len(btc_returns), len(alt_returns))
    if count < 12:
        return None, None
    x = list(btc_returns[-count:])
    y = list(alt_returns[-count:])
    mean_x = statistics.fmean(x)
    mean_y = statistics.fmean(y)
    dx = [value - mean_x for value in x]
    dy = [value - mean_y for value in y]
    var_x = sum(value * value for value in dx)
    var_y = sum(value * value for value in dy)
    if var_x <= 0 or var_y <= 0:
        return None, None
    covariance = sum(a * b for a, b in zip(dx, dy, strict=True))
    correlation = covariance / math.sqrt(var_x * var_y)
    beta = covariance / var_x
    return correlation, beta


def btc_shock_z(btc: PriceSeries, touch_at: datetime, baseline_bars: int = 72) -> float | None:
    end_index = btc.index_strictly_before(touch_at)
    if end_index < baseline_bars + 1:
        return None
    latest_time = btc.closed_at[end_index]
    latest = btc.return_by_time.get(latest_time)
    if latest is None:
        return None
    baseline: list[float] = []
    for index in range(end_index - baseline_bars, end_index):
        value = btc.return_by_time.get(btc.closed_at[index])
        if value is not None:
            baseline.append(value)
    if len(baseline) < 24:
        return None
    sigma = statistics.pstdev(baseline)
    if sigma <= 0:
        return None
    return abs(latest) / sigma


def btc_volatility_3h(btc: PriceSeries, touch_at: datetime) -> float | None:
    end_index = btc.index_strictly_before(touch_at)
    if end_index < 36:
        return None
    values: list[float] = []
    for index in range(end_index - 35, end_index + 1):
        value = btc.return_by_time.get(btc.closed_at[index])
        if value is not None:
            values.append(value)
    if len(values) < 24:
        return None
    return statistics.pstdev(values)


def _direction_sign(direction: Direction) -> float:
    return 1.0 if direction == "Long" else -1.0


def _directed(value: float | None, sign: float) -> float | None:
    return None if value is None else value * sign


def _breadth(
    series_by_symbol: dict[str, PriceSeries],
    symbols: Iterable[str],
    *,
    target_symbol: str,
    touch_at: datetime,
    bars: int,
    sign: float,
) -> tuple[float | None, float | None, float | None]:
    directed_returns: list[float] = []
    for symbol in symbols:
        if symbol == target_symbol:
            continue
        series = series_by_symbol.get(symbol)
        if series is None:
            continue
        value = return_pct(series, touch_at, bars)
        if value is not None:
            directed_returns.append(value * sign)
    if not directed_returns:
        return None, None, None
    breadth = 100.0 * sum(value > 0 for value in directed_returns) / len(directed_returns)
    median = statistics.median(directed_returns)
    dispersion = statistics.pstdev(directed_returns) if len(directed_returns) > 1 else 0.0
    return breadth, median, dispersion


def segment_for(timestamp: datetime, start: datetime, calibration_days: int) -> Segment:
    elapsed_days = (timestamp - start).total_seconds() / 86400.0
    if elapsed_days < calibration_days:
        return "S1"
    if elapsed_days < calibration_days * 2:
        return "S2"
    return "S3"


def build_regime_row(
    signal: CoreSignal,
    *,
    start: datetime,
    calibration_days: int,
    series_by_symbol: dict[str, PriceSeries],
) -> RegimeRow:
    target = series_by_symbol[signal.symbol]
    btc = series_by_symbol["BTCUSDT"]
    eth = series_by_symbol["ETHUSDT"]
    sign = _direction_sign(signal.direction)

    asset15 = return_pct(target, signal.touch_at, 3)
    asset60 = return_pct(target, signal.touch_at, 12)
    btc5 = return_pct(btc, signal.touch_at, 1)
    btc15 = return_pct(btc, signal.touch_at, 3)
    btc60 = return_pct(btc, signal.touch_at, 12)
    eth15 = return_pct(eth, signal.touch_at, 3)
    eth60 = return_pct(eth, signal.touch_at, 12)

    corr3: float | None = None
    corr12: float | None = None
    beta12: float | None = None
    residual15: float | None = None
    residual60: float | None = None
    if signal.symbol != "BTCUSDT":
        btc3, alt3 = aligned_returns(btc, target, signal.touch_at, timedelta(hours=3))
        btc12, alt12 = aligned_returns(btc, target, signal.touch_at, timedelta(hours=12))
        corr3, _ = corr_beta(btc3, alt3)
        corr12, beta12 = corr_beta(btc12, alt12)
        if beta12 is not None and asset15 is not None and btc15 is not None:
            residual15 = (asset15 - beta12 * btc15) * sign
        if beta12 is not None and asset60 is not None and btc60 is not None:
            residual60 = (asset60 - beta12 * btc60) * sign

    panel_breadth15, panel_median15, panel_dispersion15 = _breadth(
        series_by_symbol,
        series_by_symbol,
        target_symbol=signal.symbol,
        touch_at=signal.touch_at,
        bars=3,
        sign=sign,
    )
    panel_breadth60, panel_median60, _ = _breadth(
        series_by_symbol,
        series_by_symbol,
        target_symbol=signal.symbol,
        touch_at=signal.touch_at,
        bars=12,
        sign=sign,
    )
    alt_breadth15, alt_median15, _ = _breadth(
        series_by_symbol,
        ALT_SYMBOLS,
        target_symbol=signal.symbol,
        touch_at=signal.touch_at,
        bars=3,
        sign=sign,
    )
    alt_breadth60, alt_median60, _ = _breadth(
        series_by_symbol,
        ALT_SYMBOLS,
        target_symbol=signal.symbol,
        touch_at=signal.touch_at,
        bars=12,
        sign=sign,
    )

    eth_minus_btc15: float | None = None
    if eth15 is not None and btc15 is not None:
        eth_minus_btc15 = (eth15 - btc15) * sign

    return RegimeRow(
        symbol=signal.symbol,
        display_symbol=DISPLAY_SYMBOLS.get(signal.symbol, signal.symbol.removesuffix("USDT")),
        direction=signal.direction,
        touch_at=signal.touch_at,
        segment=segment_for(signal.touch_at, start, calibration_days),
        outcome_05=signal.outcome_05,
        outcome_10=signal.outcome_10,
        directional_asset_15m_pct=_directed(asset15, sign),
        directional_asset_60m_pct=_directed(asset60, sign),
        directional_btc_5m_pct=_directed(btc5, sign),
        directional_btc_15m_pct=_directed(btc15, sign),
        directional_btc_60m_pct=_directed(btc60, sign),
        btc_5m_shock_z=btc_shock_z(btc, signal.touch_at),
        btc_volatility_3h_pct=btc_volatility_3h(btc, signal.touch_at),
        directional_eth_15m_pct=_directed(eth15, sign),
        directional_eth_60m_pct=_directed(eth60, sign),
        directional_eth_minus_btc_15m_pct=eth_minus_btc15,
        alt_btc_corr_3h=corr3,
        alt_btc_corr_12h=corr12,
        alt_btc_beta_12h=beta12,
        directional_alt_btc_residual_15m_pct=residual15,
        directional_alt_btc_residual_60m_pct=residual60,
        directional_panel_breadth_15m=panel_breadth15,
        directional_panel_breadth_60m=panel_breadth60,
        directional_panel_median_15m_pct=panel_median15,
        directional_panel_median_60m_pct=panel_median60,
        panel_dispersion_15m_pct=panel_dispersion15,
        directional_alt_breadth_15m=alt_breadth15,
        directional_alt_breadth_60m=alt_breadth60,
        directional_alt_median_15m_pct=alt_median15,
        directional_alt_median_60m_pct=alt_median60,
    )


def percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires values")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def build_thresholds(rows: Sequence[RegimeRow]) -> dict[str, dict[str, FeatureThreshold]]:
    by_symbol: dict[str, list[RegimeRow]] = {}
    for row in rows:
        if row.segment == "S1":
            by_symbol.setdefault(row.symbol, []).append(row)
    output: dict[str, dict[str, FeatureThreshold]] = {}
    for symbol, symbol_rows in by_symbol.items():
        local: dict[str, FeatureThreshold] = {}
        for feature, getter in FEATURES.items():
            values = [value for row in symbol_rows if (value := getter(row)) is not None]
            if len(values) < 12:
                continue
            local[feature] = FeatureThreshold(
                symbol=symbol,
                feature=feature,
                sample=len(values),
                q25=percentile(values, 0.25),
                q50=percentile(values, 0.50),
                q75=percentile(values, 0.75),
            )
        output[symbol] = local
    return output


def classify_quartile(value: float, threshold: FeatureThreshold) -> Quartile:
    if value <= threshold.q25:
        return "Q1"
    if value <= threshold.q50:
        return "Q2"
    if value <= threshold.q75:
        return "Q3"
    return "Q4"


def outcome_metrics(rows: Sequence[RegimeRow]) -> dict[str, float | int]:
    favorable = sum(row.outcome_05 == "favorable_first" for row in rows)
    adverse = sum(row.outcome_05 == "adverse_first" for row in rows)
    neither = sum(row.outcome_05 == "neither" for row in rows)
    total = len(rows)
    decisive = favorable + adverse
    return {
        "signals": total,
        "favorable": favorable,
        "adverse": adverse,
        "neither": neither,
        "favorable_percent_all": round(100.0 * favorable / total, 4) if total else 0.0,
        "favorable_percent_decisive": (
            round(100.0 * favorable / decisive, 4) if decisive else 0.0
        ),
    }


def iqr(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return percentile(values, 0.75) - percentile(values, 0.25)


def _threshold_value(
    thresholds: dict[str, FeatureThreshold], feature: str, quantile: str
) -> float | None:
    threshold = thresholds.get(feature)
    if threshold is None:
        return None
    if quantile == "q25":
        return threshold.q25
    if quantile == "q50":
        return threshold.q50
    if quantile == "q75":
        return threshold.q75
    raise ValueError(f"unsupported quantile {quantile}")


def _value(row: RegimeRow, feature: str) -> float | None:
    getter = FEATURES[feature]
    return getter(row)


def _all_present(
    row: RegimeRow, thresholds: dict[str, FeatureThreshold], features: Sequence[str]
) -> bool:
    return all(feature in thresholds and _value(row, feature) is not None for feature in features)


def _lte(
    row: RegimeRow, thresholds: dict[str, FeatureThreshold], feature: str, quantile: str
) -> bool:
    value = _value(row, feature)
    threshold = _threshold_value(thresholds, feature, quantile)
    return value is not None and threshold is not None and value <= threshold


def _gte(
    row: RegimeRow, thresholds: dict[str, FeatureThreshold], feature: str, quantile: str
) -> bool:
    value = _value(row, feature)
    threshold = _threshold_value(thresholds, feature, quantile)
    return value is not None and threshold is not None and value >= threshold


def candidate_rules() -> tuple[CandidateRule, ...]:
    return (
        CandidateRule(
            "btc_15m_most_adverse_q1",
            ("directional_btc_15m_pct",),
            lambda row, t: _lte(row, t, "directional_btc_15m_pct", "q25"),
        ),
        CandidateRule(
            "btc_adverse_high_coupling",
            ("directional_btc_15m_pct", "alt_btc_corr_3h"),
            lambda row, t: (
                _lte(row, t, "directional_btc_15m_pct", "q25")
                and _gte(row, t, "alt_btc_corr_3h", "q75")
            ),
        ),
        CandidateRule(
            "btc_adverse_high_coupling_no_residual",
            (
                "directional_btc_15m_pct",
                "alt_btc_corr_3h",
                "directional_alt_btc_residual_15m_pct",
            ),
            lambda row, t: (
                _lte(row, t, "directional_btc_15m_pct", "q25")
                and _gte(row, t, "alt_btc_corr_3h", "q75")
                and _lte(row, t, "directional_alt_btc_residual_15m_pct", "q50")
            ),
        ),
        CandidateRule(
            "btc_adverse_high_coupling_except_decoupled",
            (
                "directional_btc_15m_pct",
                "alt_btc_corr_3h",
                "directional_alt_btc_residual_15m_pct",
            ),
            lambda row, t: (
                _lte(row, t, "directional_btc_15m_pct", "q25")
                and _gte(row, t, "alt_btc_corr_3h", "q75")
                and not _gte(row, t, "directional_alt_btc_residual_15m_pct", "q75")
            ),
        ),
        CandidateRule(
            "btc_adverse_shock_high_coupling_except_decoupled",
            (
                "directional_btc_5m_pct",
                "btc_5m_shock_z",
                "alt_btc_corr_3h",
                "directional_alt_btc_residual_15m_pct",
            ),
            lambda row, t: (
                _lte(row, t, "directional_btc_5m_pct", "q25")
                and _gte(row, t, "btc_5m_shock_z", "q75")
                and _gte(row, t, "alt_btc_corr_3h", "q75")
                and not _gte(row, t, "directional_alt_btc_residual_15m_pct", "q75")
            ),
        ),
        CandidateRule(
            "panel_breadth_adverse",
            ("directional_panel_breadth_15m", "directional_panel_median_15m_pct"),
            lambda row, t: (
                _lte(row, t, "directional_panel_breadth_15m", "q25")
                and _lte(row, t, "directional_panel_median_15m_pct", "q25")
            ),
        ),
        CandidateRule(
            "btc_eth_alt_breadth_adverse",
            (
                "directional_btc_15m_pct",
                "directional_eth_15m_pct",
                "directional_alt_breadth_15m",
            ),
            lambda row, t: (
                _lte(row, t, "directional_btc_15m_pct", "q25")
                and _lte(row, t, "directional_eth_15m_pct", "q25")
                and _lte(row, t, "directional_alt_breadth_15m", "q25")
            ),
        ),
    )


def decoupling_override(row: RegimeRow, thresholds: dict[str, FeatureThreshold]) -> bool:
    required = ("directional_btc_15m_pct", "directional_alt_btc_residual_15m_pct")
    if not _all_present(row, thresholds, required):
        return False
    return _lte(row, thresholds, "directional_btc_15m_pct", "q25") and _gte(
        row, thresholds, "directional_alt_btc_residual_15m_pct", "q75"
    )


def _scope_rows(rows: Sequence[RegimeRow], scope: str) -> list[RegimeRow]:
    if scope == "S1":
        return [row for row in rows if row.segment == "S1"]
    if scope == "S2":
        return [row for row in rows if row.segment == "S2"]
    if scope == "S3":
        return [row for row in rows if row.segment == "S3"]
    if scope == "OOS":
        return [row for row in rows if row.segment in {"S2", "S3"}]
    if scope == "ALL":
        return list(rows)
    raise ValueError(f"unsupported scope: {scope}")


def build_feature_quartile_rows(
    rows: Sequence[RegimeRow],
    thresholds_by_symbol: dict[str, dict[str, FeatureThreshold]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    symbols = sorted({row.symbol for row in rows})
    for symbol in symbols:
        symbol_rows = [row for row in rows if row.symbol == symbol]
        local_thresholds = thresholds_by_symbol.get(symbol, {})
        for scope in ("S2", "S3", "OOS"):
            scoped = _scope_rows(symbol_rows, scope)
            baseline = float(outcome_metrics(scoped)["favorable_percent_all"])
            for feature, threshold in local_thresholds.items():
                getter = FEATURES[feature]
                groups: dict[Quartile, list[RegimeRow]] = {"Q1": [], "Q2": [], "Q3": [], "Q4": []}
                for row in scoped:
                    value = getter(row)
                    if value is None:
                        continue
                    groups[classify_quartile(value, threshold)].append(row)
                for quartile, group in groups.items():
                    metrics = outcome_metrics(group)
                    rate = float(metrics["favorable_percent_all"])
                    output.append(
                        {
                            "symbol": symbol,
                            "scope": scope,
                            "feature": feature,
                            "quartile": quartile,
                            **metrics,
                            "baseline_favorable_percent_all": round(baseline, 4),
                            "uplift_pp": round(rate - baseline, 4),
                        }
                    )
    return output


def build_feature_transfer(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row["scope"] != "OOS" or int(row["signals"]) == 0:
            continue
        key = (str(row["feature"]), str(row["quartile"]))
        grouped.setdefault(key, []).append(row)
    output: list[dict[str, Any]] = []
    for (feature, quartile), group in sorted(grouped.items()):
        rates = [float(row["favorable_percent_all"]) for row in group]
        uplifts = [float(row["uplift_pp"]) for row in group]
        output.append(
            {
                "feature": feature,
                "quartile": quartile,
                "assets": len(group),
                "median_signals_per_asset": round(
                    statistics.median(int(row["signals"]) for row in group), 2
                ),
                "median_asset_rate_05": round(statistics.median(rates), 4),
                "asset_rate_iqr_pp": round(iqr(rates), 4),
                "median_uplift_05_pp": round(statistics.median(uplifts), 4),
                "uplift_iqr_pp": round(iqr(uplifts), 4),
                "improved_assets": sum(value > 0 for value in uplifts),
                "unchanged_assets": sum(value == 0 for value in uplifts),
                "worsened_assets": sum(value < 0 for value in uplifts),
            }
        )
    return output


def veto_metrics(
    rows: Sequence[RegimeRow],
    *,
    rule: CandidateRule,
    thresholds: dict[str, FeatureThreshold],
) -> dict[str, Any]:
    eligible = [row for row in rows if _all_present(row, thresholds, rule.required_features)]
    blocked = [row for row in eligible if rule.predicate(row, thresholds)]
    all_good = sum(row.outcome_05 == "favorable_first" for row in eligible)
    all_bad = sum(row.outcome_05 == "adverse_first" for row in eligible)
    blocked_good = sum(row.outcome_05 == "favorable_first" for row in blocked)
    blocked_bad = sum(row.outcome_05 == "adverse_first" for row in blocked)
    blocked_neither = sum(row.outcome_05 == "neither" for row in blocked)
    bad_blocked = 100.0 * blocked_bad / all_bad if all_bad else 0.0
    good_blocked = 100.0 * blocked_good / all_good if all_good else 0.0
    decisive_blocked = blocked_good + blocked_bad
    remaining = [row for row in eligible if row not in blocked]
    baseline_rate = float(outcome_metrics(eligible)["favorable_percent_all"])
    remaining_rate = float(outcome_metrics(remaining)["favorable_percent_all"])
    return {
        "candidate": rule.name,
        "eligible_signals": len(eligible),
        "blocked_signals": len(blocked),
        "blocked_bad": blocked_bad,
        "blocked_good": blocked_good,
        "blocked_neither": blocked_neither,
        "bad_entries_blocked_percent": round(bad_blocked, 4),
        "good_entries_blocked_percent": round(good_blocked, 4),
        "net_discrimination_pp": round(bad_blocked - good_blocked, 4),
        "blocked_bad_precision_percent": (
            round(100.0 * blocked_bad / decisive_blocked, 4) if decisive_blocked else 0.0
        ),
        "veto_efficiency_ratio": (
            None if good_blocked == 0 else round(bad_blocked / good_blocked, 4)
        ),
        "baseline_favorable_percent_all": round(baseline_rate, 4),
        "remaining_favorable_percent_all": round(remaining_rate, 4),
        "remaining_uplift_pp": round(remaining_rate - baseline_rate, 4),
    }


def build_veto_rows(
    rows: Sequence[RegimeRow],
    thresholds_by_symbol: dict[str, dict[str, FeatureThreshold]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    rules = candidate_rules()
    symbols = sorted({row.symbol for row in rows})
    for symbol in symbols:
        symbol_rows = [row for row in rows if row.symbol == symbol]
        local_thresholds = thresholds_by_symbol.get(symbol, {})
        for scope in ("S2", "S3", "OOS"):
            scoped = _scope_rows(symbol_rows, scope)
            for rule in rules:
                metrics = veto_metrics(scoped, rule=rule, thresholds=local_thresholds)
                output.append({"symbol": symbol, "scope": scope, **metrics})
            eligible_override = [
                row
                for row in scoped
                if _all_present(
                    row,
                    local_thresholds,
                    ("directional_btc_15m_pct", "directional_alt_btc_residual_15m_pct"),
                )
            ]
            override_rows = [
                row
                for row in eligible_override
                if decoupling_override(row, local_thresholds)
            ]
            override_metrics = outcome_metrics(override_rows)
            output.append(
                {
                    "symbol": symbol,
                    "scope": scope,
                    "candidate": "decoupling_override_state",
                    "eligible_signals": len(eligible_override),
                    "blocked_signals": len(override_rows),
                    "blocked_bad": int(override_metrics["adverse"]),
                    "blocked_good": int(override_metrics["favorable"]),
                    "blocked_neither": int(override_metrics["neither"]),
                    "bad_entries_blocked_percent": "",
                    "good_entries_blocked_percent": "",
                    "net_discrimination_pp": "",
                    "blocked_bad_precision_percent": "",
                    "veto_efficiency_ratio": "",
                    "baseline_favorable_percent_all": outcome_metrics(eligible_override)[
                        "favorable_percent_all"
                    ],
                    "remaining_favorable_percent_all": override_metrics[
                        "favorable_percent_all"
                    ],
                    "remaining_uplift_pp": "",
                }
            )
    return output


def build_veto_transfer(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["scope"] != "OOS" or row["candidate"] == "decoupling_override_state":
            continue
        if int(row["eligible_signals"]) == 0:
            continue
        grouped.setdefault(str(row["candidate"]), []).append(row)
    output: list[dict[str, Any]] = []
    for candidate, group in sorted(grouped.items()):
        bad = [float(row["bad_entries_blocked_percent"]) for row in group]
        good = [float(row["good_entries_blocked_percent"]) for row in group]
        discrimination = [float(row["net_discrimination_pp"]) for row in group]
        uplift = [float(row["remaining_uplift_pp"]) for row in group]
        output.append(
            {
                "candidate": candidate,
                "assets": len(group),
                "median_blocked_signals": round(
                    statistics.median(int(row["blocked_signals"]) for row in group), 2
                ),
                "median_bad_entries_blocked_percent": round(statistics.median(bad), 4),
                "median_good_entries_blocked_percent": round(statistics.median(good), 4),
                "median_net_discrimination_pp": round(statistics.median(discrimination), 4),
                "discrimination_iqr_pp": round(iqr(discrimination), 4),
                "median_remaining_uplift_pp": round(statistics.median(uplift), 4),
                "positive_discrimination_assets": sum(value > 0 for value in discrimination),
                "neutral_assets": sum(value == 0 for value in discrimination),
                "negative_discrimination_assets": sum(value < 0 for value in discrimination),
            }
        )
    return output


def build_segment_rows(
    rows: Sequence[RegimeRow],
    thresholds_by_symbol: dict[str, dict[str, FeatureThreshold]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    rules = {rule.name: rule for rule in candidate_rules()}
    tracked_rules = (
        "btc_15m_most_adverse_q1",
        "btc_adverse_high_coupling_except_decoupled",
        "panel_breadth_adverse",
        "btc_eth_alt_breadth_adverse",
    )
    for symbol in sorted({row.symbol for row in rows}):
        symbol_rows = [row for row in rows if row.symbol == symbol]
        local_thresholds = thresholds_by_symbol.get(symbol, {})
        for segment in ("S1", "S2", "S3"):
            scoped = [row for row in symbol_rows if row.segment == segment]
            metrics = outcome_metrics(scoped)
            record: dict[str, Any] = {"symbol": symbol, "segment": segment, **metrics}
            for feature in (
                "directional_btc_15m_pct",
                "directional_eth_15m_pct",
                "alt_btc_corr_3h",
                "directional_alt_btc_residual_15m_pct",
                "directional_panel_breadth_15m",
                "directional_panel_median_15m_pct",
            ):
                values = [value for row in scoped if (value := FEATURES[feature](row)) is not None]
                record[f"median_{feature}"] = (
                    "" if not values else round(statistics.median(values), 6)
                )
            for name in tracked_rules:
                rule = rules[name]
                eligible = [
                    row
                    for row in scoped
                    if _all_present(row, local_thresholds, rule.required_features)
                ]
                selected = [row for row in eligible if rule.predicate(row, local_thresholds)]
                share = 100.0 * len(selected) / len(eligible) if eligible else 0.0
                record[f"share_{name}_pct"] = round(share, 4)
            override_eligible = [
                row
                for row in scoped
                if _all_present(
                    row,
                    local_thresholds,
                    ("directional_btc_15m_pct", "directional_alt_btc_residual_15m_pct"),
                )
            ]
            overrides = [
                row for row in override_eligible if decoupling_override(row, local_thresholds)
            ]
            record["share_decoupling_override_pct"] = (
                round(100.0 * len(overrides) / len(override_eligible), 4)
                if override_eligible
                else 0.0
            )
            output.append(record)
    return output


def build_segment_transfer(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for segment in ("S1", "S2", "S3"):
        group = [row for row in rows if row["segment"] == segment]
        rates = [float(row["favorable_percent_all"]) for row in group]
        output.append(
            {
                "segment": segment,
                "assets": len(group),
                "median_asset_core_05": round(statistics.median(rates), 4) if rates else 0.0,
                "asset_core_iqr_pp": round(iqr(rates), 4),
                "median_btc_adverse_share_pct": round(
                    statistics.median(
                        float(row["share_btc_15m_most_adverse_q1_pct"]) for row in group
                    ),
                    4,
                )
                if group
                else 0.0,
                "median_broad_market_adverse_share_pct": round(
                    statistics.median(
                        float(row["share_panel_breadth_adverse_pct"]) for row in group
                    ),
                    4,
                )
                if group
                else 0.0,
                "median_decoupling_override_share_pct": round(
                    statistics.median(float(row["share_decoupling_override_pct"]) for row in group),
                    4,
                )
                if group
                else 0.0,
            }
        )
    return output


def _csv_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return round(value, 8)
    return value


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(value) for key, value in row.items()})


def _row_to_dict(row: RegimeRow) -> dict[str, Any]:
    return {key: _csv_value(value) for key, value in asdict(row).items()}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_source_manifest(paths: Sequence[tuple[str, str, Path]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for symbol, role, path in paths:
        rows.append(
            {
                "symbol": symbol,
                "role": role,
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return rows


def markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "# P44 FULL PANEL MARKET REGIME V2",
        "",
        f"Frozen interval: `{summary['evaluation_start']}` -> `{summary['evaluation_end']}`",
        "",
        "Research-only. No Entry/live rule is changed by this run.",
        "All market features are computed from fully closed 5m candles "
        "strictly before exact touch.",
        "S1 is calibration only; S2+S3 are the primary OOS evaluation.",
        "BTC.D/TOTAL3/USDT.D are intentionally not fabricated; "
        "internal panel breadth is reported separately.",
        "",
        "## OOS candidate veto transfer",
        "",
        "| candidate | assets | median bad blocked | median good blocked | discrimination | "
        "positive assets | negative assets |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in cast(list[dict[str, Any]], summary["veto_transfer"]):
        lines.append(
            f"| {row['candidate']} | {row['assets']} | "
            f"{row['median_bad_entries_blocked_percent']}% | "
            f"{row['median_good_entries_blocked_percent']}% | "
            f"{row['median_net_discrimination_pp']} pp | "
            f"{row['positive_discrimination_assets']} | {row['negative_discrimination_assets']} |"
        )
    lines.extend(
        [
            "",
            "## Three 30-day segments",
            "",
            "| segment | assets | median core +0.5/-1 | BTC adverse share | "
            "broad adverse share | decoupling share |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in cast(list[dict[str, Any]], summary["segment_transfer"]):
        lines.append(
            f"| {row['segment']} | {row['assets']} | {row['median_asset_core_05']}% | "
            f"{row['median_btc_adverse_share_pct']}% | "
            f"{row['median_broad_market_adverse_share_pct']}% | "
            f"{row['median_decoupling_override_share_pct']}% |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- Candidate thresholds are feature-distribution quartiles fixed on S1; "
            "outcomes do not set thresholds.",
            "- S2 and S3 are not used to calibrate thresholds.",
            "- Full feature quartile matrices are exported; "
            "no best quartile is promoted automatically.",
            "- Internal breadth is the frozen nine-asset panel, not TOTAL3 "
            "and not a substitute for dominance data.",
            "- A candidate veto must survive cross-asset and OOS review "
            "before any live enforcement discussion.",
            "",
        ]
    )
    return "\n".join(lines)


def create_result_zip(output_dir: Path) -> Path:
    target = output_dir.parent / f"{output_dir.name}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.glob("*")):
            if path.is_file():
                archive.write(path, arcname=f"{output_dir.name}/{path.name}")
    return target


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P44 full-panel market-regime research")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--start", default="2026-05-18T00:00:00+00:00")
    parser.add_argument("--end", default="2026-08-16T00:00:00+00:00")
    parser.add_argument("--calibration-days", type=int, default=30)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def run_analysis(
    *,
    root: Path,
    start: datetime,
    end: datetime,
    calibration_days: int,
    output_dir: Path,
    force: bool,
) -> dict[str, Any]:
    if calibration_days <= 0:
        raise ValueError("calibration_days must be positive")
    if end <= start + timedelta(days=calibration_days * 2):
        raise ValueError("evaluation interval must contain S1, S2, and S3")
    complete_path = output_dir / "RUN_COMPLETE.json"
    if complete_path.is_file() and not force:
        print(f"P44 already complete; reuse: {output_dir}")
        return cast(
            dict[str, Any],
            json.loads((output_dir / "summary.json").read_text(encoding="utf-8")),
        )

    source_paths: list[tuple[str, str, Path]] = []
    price_paths: dict[str, Path] = {}
    signal_paths: dict[str, Path] = {}
    print("P44 PRECHECK - frozen local inputs only; network is not used")
    for symbol in DEFAULT_SYMBOLS:
        asset_root = validation_root(root, symbol, start, end)
        price_path = asset_root / "p30" / "dataset" / "trade_5m.csv"
        signal_path = asset_root / "p40" / "absorption_features.csv"
        if not price_path.is_file():
            raise FileNotFoundError(f"missing frozen 5m data for {symbol}: {price_path}")
        if not signal_path.is_file():
            raise FileNotFoundError(f"missing completed P40 core for {symbol}: {signal_path}")
        price_paths[symbol] = price_path
        signal_paths[symbol] = signal_path
        source_paths.append((symbol, "trade_5m", price_path))
        source_paths.append((symbol, "p40_core", signal_path))
        print(f"  OK {symbol}: trade_5m + P40 core")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_rows = build_source_manifest(source_paths)
    write_csv(output_dir / "source_manifest.csv", manifest_rows)

    series_by_symbol: dict[str, PriceSeries] = {}
    for index, symbol in enumerate(DEFAULT_SYMBOLS, start=1):
        print(f"LOAD 5M {index}/{len(DEFAULT_SYMBOLS)}: {symbol}")
        series_by_symbol[symbol] = load_price_series(price_paths[symbol], symbol=symbol)

    all_rows: list[RegimeRow] = []
    signal_counts: dict[str, int] = {}
    for index, symbol in enumerate(DEFAULT_SYMBOLS, start=1):
        signals = load_core_signals(signal_paths[symbol], symbol=symbol, start=start, end=end)
        signal_counts[symbol] = len(signals)
        print(f"FEATURES {index}/{len(DEFAULT_SYMBOLS)}: {symbol} core={len(signals)}")
        for signal in signals:
            all_rows.append(
                build_regime_row(
                    signal,
                    start=start,
                    calibration_days=calibration_days,
                    series_by_symbol=series_by_symbol,
                )
            )

    all_rows.sort(key=lambda row: (row.touch_at, row.symbol))
    thresholds_by_symbol = build_thresholds(all_rows)
    threshold_rows = [
        asdict(threshold)
        for symbol in DEFAULT_SYMBOLS
        for threshold in thresholds_by_symbol.get(symbol, {}).values()
    ]
    feature_quartiles = build_feature_quartile_rows(all_rows, thresholds_by_symbol)
    feature_transfer = build_feature_transfer(feature_quartiles)
    veto_rows = build_veto_rows(all_rows, thresholds_by_symbol)
    veto_transfer = build_veto_transfer(veto_rows)
    segment_rows = build_segment_rows(all_rows, thresholds_by_symbol)
    segment_transfer = build_segment_transfer(segment_rows)

    write_csv(output_dir / "regime_features.csv", [_row_to_dict(row) for row in all_rows])
    write_csv(output_dir / "calibration_thresholds.csv", threshold_rows)
    write_csv(output_dir / "feature_quartiles_by_asset.csv", feature_quartiles)
    write_csv(output_dir / "feature_transfer_oos.csv", feature_transfer)
    write_csv(output_dir / "veto_candidates_by_asset.csv", veto_rows)
    write_csv(output_dir / "veto_transfer_oos.csv", veto_transfer)
    write_csv(output_dir / "segment_regime_by_asset.csv", segment_rows)
    write_csv(output_dir / "segment_transfer.csv", segment_transfer)

    summary: dict[str, Any] = {
        "architecture": "p44_full_panel_market_regime_v2",
        "research_only": True,
        "evaluation_start": start.isoformat(),
        "evaluation_end": end.isoformat(),
        "calibration": {
            "segment": "S1",
            "days": calibration_days,
            "threshold_source": "feature distributions only; no outcome optimization",
            "primary_oos": "S2+S3",
        },
        "data": {
            "network_used": False,
            "price_source": "frozen local p30/dataset/trade_5m.csv",
            "signal_source": "completed P40 core absorption_features.csv",
            "lookahead": "none; candle close must be strictly before exact touch",
            "included": [
                "BTC 5m/15m/60m direction and shock",
                "ETH direction and ETH-vs-BTC relative strength",
                "ALT/BTC rolling correlation and beta",
                "directional ALT residual vs BTC beta",
                "internal nine-asset panel breadth and alt breadth",
            ],
            "not_included": ["BTC.D", "TOTAL/TOTAL2/TOTAL3", "USDT.D/stablecoin dominance"],
        },
        "symbols": list(DEFAULT_SYMBOLS),
        "signal_counts": signal_counts,
        "feature_rows": len(all_rows),
        "veto_transfer": veto_transfer,
        "segment_transfer": segment_transfer,
        "guardrails": [
            "No P44 output changes Entry V1, live execution, risk, stop, or exit logic.",
            "S1 thresholds are fixed before evaluating S2 and S3.",
            "No outcome is used to choose a feature threshold.",
            "All feature quartiles are exported to prevent cherry-picking a best state.",
            "Internal breadth is explicitly not labeled TOTAL3 or dominance.",
        ],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.md").write_text(markdown_summary(summary), encoding="utf-8")
    complete_payload = {
        "architecture": summary["architecture"],
        "complete": True,
        "feature_rows": len(all_rows),
        "finished_at": datetime.now(tz=UTC).isoformat(),
    }
    complete_path.write_text(
        json.dumps(complete_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    result_zip = create_result_zip(output_dir)
    print(f"P44 COMPLETE: features={len(all_rows)}")
    print(f"Summary: {output_dir / 'summary.md'}")
    print(f"Result ZIP: {result_zip}")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    start = parse_datetime(str(args.start))
    end = parse_datetime(str(args.end))
    output_dir = (
        args.output_dir.resolve()
        if args.output_dir is not None
        else root
        / "reports"
        / "market_regime_p44_full_panel"
        / f"ENTRY_V1_{start:%Y%m%d}_{end:%Y%m%d}"
    )
    run_analysis(
        root=root,
        start=start,
        end=end,
        calibration_days=int(args.calibration_days),
        output_dir=output_dir,
        force=bool(args.force),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
