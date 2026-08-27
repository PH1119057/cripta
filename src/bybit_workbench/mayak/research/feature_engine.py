from __future__ import annotations

import bisect
import csv
import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from bybit_workbench.mayak.core.features import (
    acceleration,
    breadth,
    directional_agreement,
    normalized_displacement,
    realised_volatility,
    simple_return,
    synchronization,
    velocity,
)
from bybit_workbench.mayak.research.event_truth import NormalizedEntryEvent
from bybit_workbench.mayak.research.universe import DISCOVERY_SYMBOLS, PERIOD_TAG

WINDOW_OFFSETS_MINUTES = (30, 15, 10, 5, 1, 0)


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    version: str = "mayak-features.1"
    source_granularity_minutes: int = 5
    short_lookback_minutes: int = 5
    displacement_lookback_minutes: int = 30
    normalization_lookback_minutes: int = 360
    synchronization_lookback_minutes: int = 30
    persistence_lookback_minutes: int = 120
    persistence_thresholds: tuple[float, ...] = (0.6, 0.7, 0.8)
    closed_bars_only: bool = True

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Bar:
    opened_at: datetime
    closed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class ClosedBarSeries:
    def __init__(self, bars: tuple[Bar, ...]) -> None:
        if not bars:
            raise ValueError("bar series cannot be empty")
        self.bars = bars
        self.closed_epochs = tuple(bar.closed_at.timestamp() for bar in bars)

    def index_at(self, cutoff: datetime) -> int:
        """Last fully closed bar at cutoff; never exposes the current bar."""
        return bisect.bisect_right(self.closed_epochs, cutoff.timestamp()) - 1

    def slice_ending(self, cutoff: datetime, count: int) -> tuple[Bar, ...]:
        index = self.index_at(cutoff)
        if index < count - 1:
            raise ValueError(f"insufficient causal warmup at {cutoff.isoformat()}")
        return self.bars[index - count + 1 : index + 1]


def load_discovery_series(root: Path) -> dict[str, ClosedBarSeries]:
    result: dict[str, ClosedBarSeries] = {}
    for symbol in DISCOVERY_SYMBOLS:
        summary_path = (
            root
            / "reports"
            / "cross_asset_validation"
            / f"{symbol}_{PERIOD_TAG}"
            / "p40"
            / "summary.json"
        )
        summary = cast(dict[str, Any], json.loads(summary_path.read_text(encoding="utf-8")))
        dataset_dir = Path(str(summary["dataset_dir"]))
        bars: list[Bar] = []
        with (dataset_dir / "trade_5m.csv").open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                bars.append(
                    Bar(
                        opened_at=datetime.fromisoformat(str(row["opened_at"])).astimezone(UTC),
                        closed_at=datetime.fromisoformat(str(row["closed_at"])).astimezone(UTC),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    )
                )
        result[symbol] = ClosedBarSeries(tuple(bars))
    return result


def compute_event_features(
    events: tuple[NormalizedEntryEvent, ...],
    series: dict[str, ClosedBarSeries],
    spec: FeatureSpec,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        for offset in WINDOW_OFFSETS_MINUTES:
            cutoff = event.anchor_time - timedelta(minutes=offset)
            rows.append(_snapshot(event, cutoff, offset, series, spec))
    return rows


def _snapshot(
    event: NormalizedEntryEvent,
    cutoff: datetime,
    offset: int,
    series: dict[str, ClosedBarSeries],
    spec: FeatureSpec,
) -> dict[str, Any]:
    short_returns: dict[str, float] = {}
    thirty_returns: dict[str, float] = {}
    return_series: dict[str, tuple[float, ...]] = {}
    agreement_history: list[float] = []
    component_rows: dict[str, dict[str, float]] = {}
    normalization_bars = spec.normalization_lookback_minutes // 5 + 1
    for symbol, bar_series in series.items():
        bars = bar_series.slice_ending(cutoff, normalization_bars)
        closes = [bar.close for bar in bars]
        returns = tuple(
            simple_return(left, right)
            for left, right in zip(closes, closes[1:], strict=False)
        )
        short_returns[symbol] = returns[-1]
        thirty_returns[symbol] = simple_return(closes[-7], closes[-1])
        return_series[symbol] = returns[-6:]
        recent_volumes = [bar.volume for bar in bars[:-1]]
        median_volume = statistics.median(recent_volumes)
        component_rows[symbol] = {
            "return_5m": returns[-1],
            "return_30m": thirty_returns[symbol],
            "range_5m": (bars[-1].high - bars[-1].low) / bars[-1].open,
            "realised_volatility_30m": realised_volatility(closes[-7:]),
            "velocity_30m": velocity(returns[-6:]),
            "acceleration_30m": acceleration(returns[-6:]),
            "normalized_volume": bars[-1].volume / median_volume if median_volume else 0.0,
            "local_deviation": closes[-1] / statistics.median(closes[-13:]) - 1.0,
            "normalized_displacement": normalized_displacement(
                thirty_returns[symbol], returns
            ),
        }
    history_length = spec.persistence_lookback_minutes // 5
    for history_offset in range(history_length - 1, -1, -1):
        historical: dict[str, float] = {}
        history_cutoff = cutoff - timedelta(minutes=5 * history_offset)
        for symbol, bar_series in series.items():
            bars = bar_series.slice_ending(history_cutoff, 2)
            historical[symbol] = simple_return(bars[0].close, bars[1].close)
        agreement_history.append(directional_agreement(historical))
    all_breadth = breadth(short_returns)
    ex_breadth = breadth(short_returns, excluded_symbol=event.symbol)
    market_direction = statistics.median(short_returns.values())
    row: dict[str, Any] = {
        "event_id": event.event_id,
        "symbol": event.symbol,
        "side": event.side,
        "anchor_time": event.anchor_time.isoformat(),
        "feature_cutoff": cutoff.isoformat(),
        "window_offset_minutes": offset,
        "primary_label": event.primary_label.value,
        "isolated_or_clustered": str(event.cluster_metadata["isolated_or_clustered"]),
        "portfolio_hour_severity": int(event.portfolio_metadata["portfolio_hour_severity"]),
        "portfolio_day_severity": int(event.portfolio_metadata["portfolio_day_severity"]),
        "feature_spec_fingerprint": spec.fingerprint,
        "market_direction": market_direction,
        "market_velocity": statistics.median(
            item["velocity_30m"] for item in component_rows.values()
        ),
        "market_acceleration": statistics.median(
            item["acceleration_30m"] for item in component_rows.values()
        ),
        "directional_agreement": directional_agreement(short_returns),
        "synchronization": synchronization(return_series),
        "dispersion": all_breadth["dispersion"],
        "market_normalized_displacement": statistics.median(
            item["normalized_displacement"] for item in component_rows.values()
        ),
    }
    for prefix, components in (
        ("btc", component_rows["BTCUSDT"]),
        ("eth", component_rows["ETHUSDT"]),
    ):
        row.update({f"{prefix}_{key}": value for key, value in components.items()})
    row.update({f"breadth_all_{key}": value for key, value in all_breadth.items()})
    row.update({f"breadth_ex_symbol_{key}": value for key, value in ex_breadth.items()})
    for threshold in spec.persistence_thresholds:
        suffix = str(int(threshold * 100))
        count = 0
        for value in reversed(agreement_history):
            if value < threshold:
                break
            count += 1
        row[f"synchronization_persistence_{suffix}_minutes"] = count * 5.0
    return row


def write_feature_outputs(output: Path, rows: list[dict[str, Any]], spec: FeatureSpec) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "MAYAK_FEATURE_SPEC.json").write_text(
        json.dumps({**asdict(spec), "fingerprint": spec.fingerprint}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    with (output / "MAYAK_ALL9_CAUSAL_FEATURES.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
