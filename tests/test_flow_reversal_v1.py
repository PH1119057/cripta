from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.research.flow_reversal_v1 import (
    FlowReversalConfig,
    TradeDay,
    _analyse_touch,
    _directional_delta_pct,
    _find_touch_index,
    _flow_features_for_touch,
    _pair_result,
)
from bybit_workbench.research.mtf_entry_v3 import EntrySignalV3, FlowBucket


def _signal(direction: str = "Long") -> EntrySignalV3:
    typed_direction = "Long" if direction == "Long" else "Short"
    return EntrySignalV3(
        symbol="UNIUSDT",
        direction=typed_direction,
        entry_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
        entry_price=Decimal("100"),
        hourly_context="Neutral",
        hourly_return_percent=Decimal("0"),
        hourly_alignment="neutral",
        fifteen_zone_low=Decimal("99"),
        fifteen_zone_high=Decimal("101"),
        five_zone_low=Decimal("99.5"),
        five_zone_high=Decimal("100"),
        zone_gap_percent=Decimal("0"),
        hourly_effective_lookback=0,
        fifteen_effective_lookback=20,
        five_effective_lookback=60,
        hourly_regime_reset_at=None,
        fifteen_regime_reset_at=None,
        five_regime_reset_at=None,
        outcome_metrics={
            "first_0_5_vs_1_0": "favorable_first",
            "hit_plus_0_5_pct": 1,
            "hit_plus_1_pct": 1,
        },
    )


def test_directional_delta_is_mirrored_for_short() -> None:
    buy = Decimal("75")
    sell = Decimal("25")
    assert _directional_delta_pct("Long", buy, sell) == Decimal("50")
    assert _directional_delta_pct("Short", buy, sell) == Decimal("-50")


def test_find_touch_index_resolves_first_trade_inside_candidate_bar() -> None:
    start = datetime(2026, 8, 16, 12, 0, tzinfo=UTC).timestamp()
    timestamps = tuple(start + value for value in (10, 70, 130, 190, 250))
    prices = (101.0, 100.4, 99.9, 99.8, 100.2)
    assert (
        _find_touch_index(
            timestamps,
            prices,
            direction="Long",
            entry_price=100.0,
            window_start=start,
            window_end=start + 300,
        )
        == 2
    )


def test_touch_aligned_flow_detects_pressure_then_reversal() -> None:
    signal = _signal("Long")
    touch = datetime(2026, 8, 16, 12, 5, 30, tzinfo=UTC)
    mapping: dict[datetime, FlowBucket] = {}
    for minute in range(0, 4):
        opened_at = datetime(2026, 8, 16, 12, minute, tzinfo=UTC)
        mapping[opened_at] = FlowBucket(
            opened_at=opened_at,
            buy_notional=Decimal("20"),
            sell_notional=Decimal("80"),
        )
    reversal_at = datetime(2026, 8, 16, 12, 4, tzinfo=UTC)
    mapping[reversal_at] = FlowBucket(
        opened_at=reversal_at,
        buy_notional=Decimal("80"),
        sell_notional=Decimal("20"),
    )
    features = _flow_features_for_touch(
        signal,
        touch,
        mapping,
        config=FlowReversalConfig(),
    )
    assert features.pressure_directional_delta_pct == Decimal("-60")
    assert features.reversal_directional_delta_pct == Decimal("60")
    assert features.reversal_strength_pct == Decimal("120")
    assert features.flow_state == "pressure_then_reversal"


def test_raw_tape_exact_order_ignores_pre_touch_high() -> None:
    signal = _signal("Long")
    start = signal.entry_at.timestamp()
    # Price first runs above +0.5%, then only later touches the 100 limit. P30 OHLC can
    # falsely treat that earlier high as favorable after entry; P31 must start at touch.
    timestamps = tuple(start + value for value in (10, 30, 70, 90, 120, 150))
    prices = (100.8, 100.6, 99.9, 99.4, 98.9, 100.7)
    result = _analyse_touch(
        signal,
        TradeDay(timestamps=timestamps, prices=prices),
        config=FlowReversalConfig(exact_horizon_minutes=5, immediate_mfe_mae_minutes=5),
        data_end=signal.entry_at + timedelta(minutes=10),
    )
    assert result.touch_at == datetime.fromtimestamp(start + 70, UTC)
    assert result.exact_first_0_5_vs_1_0 == "adverse_first"
    assert result.seconds_to_minus_1_0 == 50
    assert result.seconds_to_plus_0_5 == 80


def test_pair_result_marks_missing_tail_as_incomplete() -> None:
    assert _pair_result(None, None, complete=False) == "incomplete"
    assert _pair_result(None, None, complete=True) == "neither"
