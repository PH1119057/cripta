"""P90L90 Entry candidate identity must match the existing MarketWatch route."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.universal_entry.contracts import (
    FrozenPolicy,
    MarketFactEnvelope,
    TradeDirection,
)
from bybit_workbench.universal_entry.fingerprint import fingerprint
from bybit_workbench.universal_entry.p90l90_causal import (
    P90L90Crossing,
    P90L90EntryState,
)
from bybit_workbench.universal_entry.p90l90_crossing_fact import (
    p90l90_crossing_fact,
)

AT = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)


def _state() -> P90L90EntryState:
    return P90L90EntryState(
        effective_at=AT,
        l53_low_raw=Decimal("100"), l53_high_raw=Decimal("112"),
        h9_5m_low_raw=Decimal("99"), h9_5m_high_raw=Decimal("115"),
        h9_15m_low_raw=Decimal("99"), h9_15m_high_raw=Decimal("115"),
        raw_h9_union_span=Decimal("16"),
        working_width_pct=Decimal("0.9"),
        previous_seven_day_median=Decimal("0.95"),
        long_depth_pct=Decimal("6.25"), short_depth_pct=Decimal("18.75"),
    )


def _trade() -> MarketFactEnvelope:
    at = AT + timedelta(seconds=4)
    return MarketFactEnvelope(
        fact_id="trade-LTC-abc", event_kind="PUBLIC_TRADE",
        symbol="LTCUSDT", observed_at=at, event_at=at,
        received_at=at+timedelta(milliseconds=50),
        source_refs=("bybit:publicTrade:LTCUSDT:abc",),
        attributes=FrozenPolicy.from_mapping({
            "price": "99.5", "size": "7", "taker_side": "Sell",
        }),
    )


def _crossing() -> P90L90Crossing:
    return P90L90Crossing(
        side="LONG", event_at=_trade().event_at, effective_at=AT,
        current_price=Decimal("99.5"), previous_price=Decimal("100.5"),
        boundary=Decimal("100"), depth_pct=Decimal("6.25"),
    )


def test_crossing_fact_exact_marketwatch_fingerprint_and_nested_payload():
    source = _trade()
    geometry = _state()
    result = p90l90_crossing_fact(
        entry_plan_fingerprint="entry-plan-123",
        source_fact=source,
        crossing=_crossing(),
        geometry=geometry,
    )
    expected = "watch-p90l90-" + fingerprint({
        "entry_plan_fingerprint": "entry-plan-123",
        "source_fact": source.fact_id,
        "direction": "LONG",
        "effective_at": AT,
    })[:32]
    assert result.fact_id == expected
    assert result.event_kind == "P90L90_RAW_H9_PREV7D_CROSSING"
    assert result.direction is TradeDirection.LONG
    assert result.source_refs == source.source_refs
    assert result.observed_at == source.observed_at
    assert result.event_at == source.event_at
    assert result.attributes.to_dict() == FrozenPolicy.from_mapping({
        "entry_price": Decimal("99.5"),
        "price": Decimal("99.5"),
        "previous_tick_price": Decimal("100.5"),
        "raw_entry_boundary": Decimal("100"),
        "depth_pct": Decimal("6.25"),
        "state_effective_at": AT,
        "p90l90": {
            "candidate_price": Decimal("99.5"),
            "opposite_raw_l53_target": Decimal("112"),
            "state_effective_at": AT,
            "depth_pct": Decimal("6.25"),
        },
        "modeled_exchange_fill": False,
        "trade_fact_id": source.fact_id,
    }).to_dict()


def test_reject_forged_crossing_and_future_geometry():
    good = _crossing()
    s = _state()
    from dataclasses import replace

    with pytest.raises(ValueError, match="causal geometry"):
        p90l90_crossing_fact(
            entry_plan_fingerprint="fp", source_fact=_trade(),
            crossing=replace(good, effective_at=AT+timedelta(minutes=5)),
            geometry=s,
        )
    with pytest.raises(ValueError, match="frozen raw Entry"):
        p90l90_crossing_fact(
            entry_plan_fingerprint="fp", source_fact=_trade(),
            crossing=replace(good, boundary=Decimal("99")),
            geometry=s,
        )
    with pytest.raises(ValueError, match="native trade price"):
        p90l90_crossing_fact(
            entry_plan_fingerprint="fp", source_fact=_trade(),
            crossing=replace(good, current_price=Decimal("99")),
            geometry=s,
        )
