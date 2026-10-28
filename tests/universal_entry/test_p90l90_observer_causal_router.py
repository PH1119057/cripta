"""Pure P90 observer-router isolation: ordinary events never wait for P90 bars."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
)
from bybit_workbench.universal_entry.p90l90_observer_causal_router import (
    P90L90ObserverCausalRouter,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

B = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)


def trade(symbol: str = "LTCUSDT"):
    at = B+timedelta(seconds=1)
    fact = MarketFactEnvelope(
        fact_id=f"trade-{symbol}", event_kind="PUBLIC_TRADE",
        symbol=symbol, observed_at=at, event_at=at,
        received_at=at+timedelta(seconds=2),
        source_refs=(f"bybit:publicTrade:{symbol}:1",),
        attributes=FrozenPolicy.from_mapping({
            "price":"100","size":"1","taker_side":"Buy",
        }),
    )
    return fact, TradeCursor(symbol, "e1", 1, at)


def bar(tf: str):
    return MarketFactEnvelope(
        fact_id=f"bar-{tf}", event_kind="CANDLE_CLOSED",
        symbol="LTCUSDT", observed_at=B, event_at=B,
        received_at=B+timedelta(seconds=3),
        source_refs=(f"bybit:kline.{tf}:LTCUSDT:1",),
        attributes=FrozenPolicy.from_mapping({
            "timeframe":tf,
            "opened_at":(B-timedelta(minutes=int(tf))).isoformat(),
            "closed_at":B.isoformat(),
            "open":"100","high":"101","low":"99","close":"100","volume":"50",
        }),
    )


class MemoryJoin:
    def __init__(self):
        self.gate = P90L90CausalTransportJoin(
            symbol="LTCUSDT", closed_5m_at=B-timedelta(minutes=5),
            closed_15m_at=B-timedelta(minutes=15),
        )
        self.calls = 0

    def deliver(self, event, *, dispatch):
        self.calls += 1
        result = self.gate.accept(event)
        dispatch(result)
        return result


def test_ordinary_strategy_receives_transport_as_is_while_p90_waits_causally():
    store = MemoryJoin()
    ordinary, p90 = [], []
    router = P90L90ObserverCausalRouter(
        store, p90_symbols=frozenset({"LTCUSDT"}),
        ordinary=lambda f,c: ordinary.append((f.fact_id,c)),
        p90_paper=lambda f,c: p90.append((f.fact_id,c)),
    )
    native, cursor = trade()
    assert router.accept(native, cursor=cursor) == 0
    assert router.accept(bar("15")) == 0
    # Non-P90 consumers were NOT delayed, restarted, or reordered.
    assert [x[0] for x in ordinary] == ["trade-LTCUSDT","bar-15"]
    assert p90 == []
    assert router.accept(bar("5")) == 3
    assert [x[0] for x in ordinary] == [
        "trade-LTCUSDT","bar-15","bar-5",
    ]
    assert [x[0] for x in p90] == ["bar-5","bar-15","trade-LTCUSDT"]
    assert p90[-1][1] == cursor
    assert all(x[1] is None for x in p90[:2])
    assert store.gate.pending == []
    assert router.accept(native, cursor=cursor) == 0
    # Normal consumer owns its independent dedupe; router never implies
    # a replayed delivery was an independently authorized R1 signal.
    assert [x[0] for x in p90] == ["bar-5","bar-15","trade-LTCUSDT"]


def test_non_p90_and_non_causal_facts_do_not_touch_the_p90_store():
    store = MemoryJoin()
    ordinary, paper = [], []
    router = P90L90ObserverCausalRouter(
        store, p90_symbols=frozenset({"LTCUSDT"}),
        ordinary=lambda f,c: ordinary.append(f.fact_id),
        p90_paper=lambda f,c: paper.append(f.fact_id),
    )
    outside, cursor = trade("BTCUSDT")
    assert router.accept(outside, cursor=cursor) == 0
    opening = MarketFactEnvelope(
        fact_id="open", event_kind="BAR_OPEN", symbol="LTCUSDT",
        observed_at=B, event_at=B, received_at=B+timedelta(seconds=1),
        source_refs=("bybit:open",),
    )
    assert router.accept(opening) == 0
    assert store.calls == 0
    assert ordinary == ["trade-BTCUSDT", "open"]
    assert paper == []


def test_ordinary_handler_failure_never_acknowledges_p90_trade():
    store = MemoryJoin()
    def fail(f, c):
        raise RuntimeError("R1_ORDINARY_FAULT")
    router = P90L90ObserverCausalRouter(
        store, p90_symbols=frozenset({"LTCUSDT"}),
        ordinary=fail, p90_paper=lambda f,c: None,
    )
    native, cursor = trade()
    with pytest.raises(RuntimeError, match="R1_ORDINARY_FAULT"):
        router.accept(native, cursor=cursor)
    assert store.calls == 0
    assert store.gate.pending == []


def test_out_of_scope_router_configuration_rejected():
    with pytest.raises(ValueError, match="outside approved"):
        P90L90ObserverCausalRouter(
            MemoryJoin(),
            ordinary=lambda f,c: None, p90_paper=lambda f,c: None,
            p90_symbols=frozenset({"BTCUSDT"}),
        )
