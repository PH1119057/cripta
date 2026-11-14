from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bybit_workbench.research.market_regime_full_panel_v2 import (
    CoreSignal,
    FeatureThreshold,
    PriceSeries,
    _breadth,
    build_thresholds,
    classify_quartile,
    decoupling_override,
    return_pct,
)
from bybit_workbench.research.market_regime_full_panel_v2 import RegimeRow


def _series(symbol: str, closes: list[float], start: datetime) -> PriceSeries:
    times = tuple(start + timedelta(minutes=5 * index) for index in range(len(closes)))
    returns = {
        times[index]: (closes[index] / closes[index - 1] - 1.0) * 100.0
        for index in range(1, len(closes))
    }
    return PriceSeries(symbol, times, tuple(closes), returns)


def _row(symbol: str, segment: str, btc15: float, residual: float) -> RegimeRow:
    return RegimeRow(
        symbol=symbol,
        display_symbol=symbol,
        direction="Long",
        touch_at=datetime(2026, 5, 20, tzinfo=UTC),
        segment=segment,  # type: ignore[arg-type]
        outcome_05="favorable_first",
        outcome_10="favorable_first",
        directional_asset_15m_pct=0.1,
        directional_asset_60m_pct=0.2,
        directional_btc_5m_pct=-0.1,
        directional_btc_15m_pct=btc15,
        directional_btc_60m_pct=-0.2,
        btc_5m_shock_z=1.0,
        btc_volatility_3h_pct=0.1,
        directional_eth_15m_pct=-0.1,
        directional_eth_60m_pct=-0.2,
        directional_eth_minus_btc_15m_pct=0.0,
        alt_btc_corr_3h=0.8,
        alt_btc_corr_12h=0.7,
        alt_btc_beta_12h=1.2,
        directional_alt_btc_residual_15m_pct=residual,
        directional_alt_btc_residual_60m_pct=residual,
        directional_panel_breadth_15m=25.0,
        directional_panel_breadth_60m=25.0,
        directional_panel_median_15m_pct=-0.1,
        directional_panel_median_60m_pct=-0.2,
        panel_dispersion_15m_pct=0.2,
        directional_alt_breadth_15m=25.0,
        directional_alt_breadth_60m=25.0,
        directional_alt_median_15m_pct=-0.1,
        directional_alt_median_60m_pct=-0.2,
    )


def test_return_uses_only_candle_strictly_before_touch() -> None:
    start = datetime(2026, 5, 18, 0, 5, tzinfo=UTC)
    series = _series("TEST", [100.0, 101.0, 500.0], start)
    touch = start + timedelta(minutes=10)
    value = return_pct(series, touch, 1)
    assert value is not None
    assert round(value, 8) == 1.0


def test_calibration_thresholds_ignore_s2_and_s3_values() -> None:
    rows = [_row("ALT", "S1", float(index), float(index)) for index in range(20)]
    rows.extend(_row("ALT", "S2", 10_000.0, 10_000.0) for _ in range(20))
    first = build_thresholds(rows)["ALT"]["directional_btc_15m_pct"]
    rows.extend(_row("ALT", "S3", -10_000.0, -10_000.0) for _ in range(20))
    second = build_thresholds(rows)["ALT"]["directional_btc_15m_pct"]
    assert first == second


def test_decoupling_override_requires_adverse_btc_and_high_residual() -> None:
    thresholds = {
        "directional_btc_15m_pct": FeatureThreshold(
            "ALT", "directional_btc_15m_pct", 20, -1.0, 0.0, 1.0
        ),
        "directional_alt_btc_residual_15m_pct": FeatureThreshold(
            "ALT", "directional_alt_btc_residual_15m_pct", 20, -0.5, 0.0, 0.5
        ),
    }
    assert decoupling_override(_row("ALT", "S2", -2.0, 1.0), thresholds)
    assert not decoupling_override(_row("ALT", "S2", -0.5, 1.0), thresholds)
    assert not decoupling_override(_row("ALT", "S2", -2.0, 0.1), thresholds)


def test_breadth_excludes_target_symbol() -> None:
    start = datetime(2026, 5, 18, 0, 5, tzinfo=UTC)
    series = {
        "A": _series("A", [100.0, 50.0, 25.0, 12.5], start),
        "B": _series("B", [100.0, 101.0, 102.0, 103.0], start),
        "C": _series("C", [100.0, 99.0, 98.0, 97.0], start),
    }
    touch = start + timedelta(minutes=20)
    breadth, median, _ = _breadth(
        series,
        series,
        target_symbol="A",
        touch_at=touch,
        bars=1,
        sign=1.0,
    )
    assert breadth == 50.0
    assert median is not None


def test_quartile_boundaries_are_deterministic() -> None:
    threshold = FeatureThreshold("ALT", "x", 20, 1.0, 2.0, 3.0)
    assert classify_quartile(1.0, threshold) == "Q1"
    assert classify_quartile(1.5, threshold) == "Q2"
    assert classify_quartile(2.5, threshold) == "Q3"
    assert classify_quartile(4.0, threshold) == "Q4"


def test_signal_contract_fields_can_be_written(tmp_path: Path) -> None:
    path = tmp_path / "signals.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["symbol", "direction", "touch_at", "first_0_5_vs_1_0", "first_1_0_vs_1_0"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "symbol": "ALT",
                "direction": "Long",
                "touch_at": datetime(2026, 5, 18, tzinfo=UTC).isoformat(),
                "first_0_5_vs_1_0": "favorable_first",
                "first_1_0_vs_1_0": "neither",
            }
        )
    signal = CoreSignal(
        symbol="ALT",
        direction="Long",
        touch_at=datetime(2026, 5, 18, tzinfo=UTC),
        outcome_05="favorable_first",
        outcome_10="neither",
    )
    assert signal.outcome_05 == "favorable_first"
