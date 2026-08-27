from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.research.entry_open_interest_v4 import (
    OiResearchConfig,
    P33Signal,
    SeriesIndex,
    _directed_price_return_60m,
    _embargo_acceptance,
    _oi_state,
    _percent_change,
    _quartile,
    _quartile_bounds,
)


def _signal(
    *,
    minute: int = 0,
    direction: str = "Long",
    outcome: str = "favorable_first",
    minus_one_seconds: float | None = None,
) -> P33Signal:
    typed_direction = "Long" if direction == "Long" else "Short"
    typed_outcome = (
        "favorable_first"
        if outcome == "favorable_first"
        else "adverse_first"
        if outcome == "adverse_first"
        else "neither"
    )
    touch = datetime(2026, 8, 16, 12, 0, tzinfo=UTC) + timedelta(minutes=minute)
    return P33Signal(
        symbol="UNIUSDT",
        direction=typed_direction,
        candidate_bar_at=touch.replace(second=0, microsecond=0),
        entry_price=Decimal("100"),
        touch_at=touch,
        hourly_alignment="neutral",
        flow_state="pressure_then_reversal",
        exact_mae_30m_pct=Decimal("-0.4"),
        first_0_5_vs_1_0=typed_outcome,
        first_1_0_vs_1_0=typed_outcome,
        seconds_to_minus_1_0=minus_one_seconds,
        seconds_to_plus_0_5=60.0,
        seconds_to_plus_1_0=120.0,
    )


def test_config_records_price_invalidation_not_equity_risk() -> None:
    config = OiResearchConfig()
    assert config.price_invalidation_percent == Decimal("1.0")
    assert config.failure_embargo_minutes == 60


def test_percent_change() -> None:
    assert _percent_change(Decimal("110"), Decimal("100")) == Decimal("10.0")


def test_oi_state_detects_expansion_stall() -> None:
    assert (
        _oi_state(Decimal("-0.02"), Decimal("0.10"), Decimal("0.50"))
        == "expansion_stalls"
    )
    assert (
        _oi_state(Decimal("-0.02"), Decimal("-0.10"), Decimal("-0.50"))
        == "deleveraging_continues"
    )


def test_embargo_starts_after_minus_one_event() -> None:
    first = _signal(outcome="adverse_first", minus_one_seconds=60.0)
    blocked = _signal(minute=30)
    accepted = _signal(minute=62)
    result = _embargo_acceptance((first, blocked, accepted), minutes=60)
    assert result[first.touch_at]
    assert not result[blocked.touch_at]
    assert result[accepted.touch_at]


def test_quartile_assignment() -> None:
    bounds = _quartile_bounds(
        [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4"), Decimal("5")]
    )
    assert _quartile(Decimal("1"), bounds) == "Q1"
    assert _quartile(Decimal("5"), bounds) == "Q4"


def test_directed_price_return_mirrors_short() -> None:
    timestamp = datetime(2026, 8, 16, 11, 0, tzinfo=UTC)
    prices = SeriesIndex((timestamp,), (Decimal("90"),))
    long_value = _directed_price_return_60m(_signal(direction="Long"), prices)
    short_value = _directed_price_return_60m(_signal(direction="Short"), prices)
    assert long_value is not None
    assert short_value is not None
    assert long_value == -short_value
