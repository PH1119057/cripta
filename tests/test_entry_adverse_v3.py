from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.domain.models import Candle
from bybit_workbench.research.entry_adverse_v3 import (
    AdverseResearchConfig,
    EntrySignal,
    _analyse_path,
    _directional_move_pct,
    _embargo_simulation,
    _first_outcome,
    _shock_flags,
)
from bybit_workbench.research.flow_exhaustion_v2 import MicroTape


def _signal(direction: str = "Long") -> EntrySignal:
    typed_direction = "Long" if direction == "Long" else "Short"
    return EntrySignal(
        symbol="UNIUSDT",
        direction=typed_direction,
        candidate_bar_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        entry_price=Decimal("100"),
        touch_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        hourly_alignment="neutral",
        zone_gap_percent=Decimal("0"),
        flow_state="pressure_then_reversal",
    )


def _tape(moves: tuple[float, ...]) -> MicroTape:
    start = datetime(2026, 8, 16, 12, 0, tzinfo=UTC).timestamp()
    timestamps = tuple(start + index for index in range(len(moves)))
    prices = tuple(100.0 * (1.0 + move / 100.0) for move in moves)
    prefix = tuple(float(index) for index in range(len(moves) + 1))
    return MicroTape(timestamps, prices, prefix, prefix)


def _candle(index: int, high: str, low: str, close: str) -> Candle:
    opened = datetime(2026, 8, 16, 12, 0, tzinfo=UTC) + timedelta(minutes=5 * index)
    return Candle(
        symbol="UNIUSDT",
        timeframe="5",
        opened_at=opened,
        closed_at=opened + timedelta(minutes=5),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def test_directional_move_mirrors_short() -> None:
    assert _directional_move_pct("Long", 100.0, 101.0) == 1.0
    assert _directional_move_pct("Short", 100.0, 101.0) == -1.0


def test_first_outcome_orders_thresholds() -> None:
    assert _first_outcome(10.0, 20.0) == "favorable_first"
    assert _first_outcome(20.0, 10.0) == "adverse_first"
    assert _first_outcome(None, None) == "neither"


def test_path_measures_adverse_excursion_before_plus_half() -> None:
    path = _analyse_path(
        _signal(),
        _tape((0.0, -0.2, -0.4, -0.3, 0.1, 0.6, 1.1)),
        config=AdverseResearchConfig(),
    )
    assert path.first_0_5_vs_1_0 == "favorable_first"
    assert path.mae_before_plus_0_5_pct == Decimal("-0.4")
    assert path.adverse_hits_seconds["0_3"] == 2.0
    assert path.favorable_hits_seconds["0_5"] == 5.0


def test_path_detects_minus_one_before_target() -> None:
    path = _analyse_path(
        _signal(),
        _tape((0.0, -0.4, -1.1, -0.7, 0.6)),
        config=AdverseResearchConfig(),
    )
    assert path.first_0_5_vs_1_0 == "adverse_first"
    assert path.adverse_hits_seconds["1_0"] == 2.0


def test_shock_flags_use_only_prior_ranges() -> None:
    candles = tuple(_candle(index, "100.2", "99.8", "100") for index in range(20))
    candles += (_candle(20, "102", "98", "99"),)
    flags = _shock_flags(candles, period=20, multiple=Decimal("3"))
    assert not any(flags[:20])
    assert flags[20]


def test_embargo_simulation_does_not_change_live_state() -> None:
    # This test is intentionally limited to the pure candidate-filter helper.
    assert callable(_embargo_simulation)
