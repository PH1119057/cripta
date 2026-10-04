from datetime import UTC, datetime
from decimal import Decimal

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.market_watch import (
    ParameterizedCausalMarketWatch,
    _WatchState,
)


def test_disabled_flow_does_not_require_legacy_offsets() -> None:
    now = datetime(2026, 10, 4, 17, 30, tzinfo=UTC)
    fact = MarketFactEnvelope(
        fact_id="trade-1",
        event_kind="TRADE",
        symbol="APTUSDT",
        observed_at=now,
        event_at=now,
        received_at=now,
        source_refs=("test",),
        attributes=FrozenPolicy.from_mapping(
            {"price": "10", "size": "2", "taker_side": "Buy"}
        ),
    )
    watch = ParameterizedCausalMarketWatch()
    state = _WatchState()
    price, side = watch._record_flow(state, fact, {"flow": {"enabled": False}})
    assert price == Decimal("10")
    assert side == "Buy"
    assert len(state.flow) == 1
