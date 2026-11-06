"""Document the current P90 publicTrade sequence/MFE contract mismatch.

This test intentionally keeps the source and REAL MFE invariants unchanged.
A green characterization is NOT a fix. The strict XFAIL is a release blocker:
source-owned ordering proof for equal-seq/equal-ms distinct exec IDs is absent.
No asset/account observation, PostgreSQL mutation, or Bybit order is used.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from test_p90l90_real_mfe import AT, SHA, _cursor, _position

from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
    P90TransportEvent,
)
from bybit_workbench.universal_exit.p90l90_real_mfe import (
    P90RealMfeCheckpoint,
    P90RealMfeUnverified,
)


def _two_source_trades(*, second_price: str = "100.2"):
    position = _position()
    first = _cursor(position, seq=100, exec_id="original-A", millis=3)
    second = _cursor(position, seq=100, exec_id="original-B", millis=3)
    join = P90L90CausalTransportJoin(
        symbol=position.symbol,
        closed_5m_at=AT, closed_15m_at=AT,
    )
    source_events = (
        P90TransportEvent(
            event_id="synthetic-source-A", symbol=position.symbol,
            kind="PUBLIC_TRADE", event_at=first.traded_at,
            source_json='{"price":"100.1","original":"A"}',
            exchange_sequence=first.seq, exec_id=first.exec_id,
        ),
        P90TransportEvent(
            event_id="synthetic-source-B", symbol=position.symbol,
            kind="PUBLIC_TRADE", event_at=second.traded_at,
            source_json='{"price":"' + second_price + '","original":"B"}',
            exchange_sequence=second.seq, exec_id=second.exec_id,
        ),
    )
    return position, join, source_events, first, second


def test_native_join_preserves_two_distinct_ids_at_identical_seq_and_time():
    position, join, events, _, _ = _two_source_trades()
    assert join.accept(events[0]) == (events[0],)
    assert join.accept(events[1]) == (events[1],)
    assert join.last_emitted_exec_ids == {"original-A", "original-B"}
    restored = P90L90CausalTransportJoin.restore(join.snapshot())
    assert restored.accept(events[1]) == ()
    assert restored.last_emitted_exec_ids == join.last_emitted_exec_ids
    assert restored.symbol == position.symbol


@pytest.mark.parametrize("second_price", ["100.1", "100.2"])
def test_current_mfe_explicitly_rejects_unproven_tied_native_order(second_price):
    position, join, events, first, second = _two_source_trades(
        second_price=second_price,
    )
    assert join.accept(events[0]) and join.accept(events[1])
    state = P90RealMfeCheckpoint.begin(
        position=position, source_commit=SHA, first_cursor=first,
        first_price=Decimal("100.1"),
        verified_fill_to_first_trade_coverage=True,
    )
    with pytest.raises(P90RealMfeUnverified, match="NATIVE_ORDER_NOT_PROVEN"):
        state.advance(
            position=position, cursor=second, price=Decimal(second_price),
            proven_contiguous_native_cursor=True,
        )
    assert state.cursor_exec_id == first.exec_id
    assert state.best_favorable_price == Decimal("100.1")


def test_strictly_later_timestamp_within_same_seq_remains_supported():
    position = _position()
    first = _cursor(position, seq=100, exec_id="original-A", millis=3)
    later = _cursor(position, seq=100, exec_id="original-B", millis=4)
    state = P90RealMfeCheckpoint.begin(
        position=position, source_commit=SHA, first_cursor=first,
        first_price=Decimal("100.1"),
        verified_fill_to_first_trade_coverage=True,
    )
    advanced = state.advance(
        position=position, cursor=later, price=Decimal("100.2"),
        proven_contiguous_native_cursor=True,
    )
    assert advanced.cursor_exec_id == later.exec_id
    assert advanced.best_favorable_price == Decimal("100.2")


def test_proven_native_tie_order_advances_mfe_without_losing_exec_ids():
    """Source-owner proof is required; no UUID sorting or ignored native tick."""
    position, join, events, first, second = _two_source_trades()
    assert join.accept(events[0]) and join.accept(events[1])
    state = P90RealMfeCheckpoint.begin(
        position=position, source_commit=SHA, first_cursor=first,
        first_price=Decimal("100.1"),
        verified_fill_to_first_trade_coverage=True,
    )
    # The exact source-owner boundary is tested in
    # test_p90l90_real_live_source_ports.py; the pure state consumes only
    # its already-verified boolean proof.
    resolved = state.advance(
        position=position, cursor=second, price=Decimal("100.2"),
        proven_contiguous_native_cursor=True,
        proven_same_timestamp_delivery_order=True,
    )
    assert resolved.cursor_exec_id == second.exec_id
