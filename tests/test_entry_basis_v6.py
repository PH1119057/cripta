from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from bybit_workbench.research.entry_basis_v6 import (
    BasisPoint,
    BasisResearchConfig,
    BasisSeriesIndex,
    PricePoint,
    _basis_state,
    _build_basis_series,
    _directional_change,
    _directional_value,
    _quartile,
    _quartile_bounds,
)


def _basis_point(minute: int, basis_bps: str) -> BasisPoint:
    return BasisPoint(
        timestamp=datetime(2026, 8, 16, 12, minute, tzinfo=UTC),
        mark_price=Decimal("1"),
        index_price=Decimal("1"),
        basis_bps=Decimal(basis_bps),
    )


def test_config_keeps_basis_research_diagnostic() -> None:
    config = BasisResearchConfig()
    assert config.endpoint == "https://api.bybit.kz"
    assert config.interval == "5"


def test_series_anchor_uses_only_completed_previous_bar() -> None:
    rows = (_basis_point(0, "1"), _basis_point(5, "2"))
    series = BasisSeriesIndex(tuple(item.timestamp for item in rows), rows)
    assert series.point_strictly_before(rows[1].timestamp) == rows[0]


def test_basis_builds_mark_minus_index_in_basis_points() -> None:
    timestamp = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    mark = (PricePoint(timestamp, Decimal("101")),)
    index = (PricePoint(timestamp, Decimal("100")),)
    series = _build_basis_series(mark, index)
    assert series.points[0].basis_bps == Decimal("100.00")


def test_directional_value_mirrors_long_and_short() -> None:
    assert _directional_value(Decimal("3"), "Long") == Decimal("3")
    assert _directional_value(Decimal("3"), "Short") == Decimal("-3")


def test_directional_change_is_causal_and_mirrored() -> None:
    rows = (_basis_point(0, "1"), _basis_point(5, "3"))
    series = BasisSeriesIndex(tuple(item.timestamp for item in rows), rows)
    assert _directional_change(
        series,
        direction="Long",
        anchor=rows[1],
        window_minutes=5,
    ) == Decimal("2")
    assert _directional_change(
        series,
        direction="Short",
        anchor=rows[1],
        window_minutes=5,
    ) == Decimal("-2")


def test_basis_state_and_quartiles_are_descriptive() -> None:
    assert _basis_state(Decimal("1")) == "aligned_premium"
    assert _basis_state(Decimal("-1")) == "opposed_discount"
    bounds = _quartile_bounds(
        [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]
    )
    assert _quartile(Decimal("1"), bounds) == "Q1"
    assert _quartile(Decimal("5"), bounds) == "Q4"
