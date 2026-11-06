"""RED-first regression for valid Bybit publicTrade same-sequence bursts.

These tests use invented symbols and native IDs only. No DB, Bybit, Strategy,
position, account, disk or Exchange mutation. The existing 512-ID join fails.
They state required exactly-once transport behavior, not an implementation or
a Strategy policy. A future bounded durable cursor must prove these cases
without unbounded checkpoint growth or weakening restart provenance.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
    P90TransportEvent,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
SYMBOL = "SYNTHETICUSDT"


def _join() -> P90L90CausalTransportJoin:
    return P90L90CausalTransportJoin(
        symbol=SYMBOL,
        closed_5m_at=START,
        closed_15m_at=START,
    )


def _trade(index: int, *, seq: int = 17) -> P90TransportEvent:
    return P90TransportEvent(
        event_id=f"synthetic-{seq}-{index}",
        symbol=SYMBOL,
        kind="PUBLIC_TRADE",
        event_at=START + timedelta(milliseconds=index),
        source_json='{"price":"1","size":"1"}',
        exchange_sequence=seq,
        exec_id=f"exec-{seq}-{index}",
    )


def test_one_valid_message_can_have_1024_distinct_trades_with_one_sequence() -> None:
    """Bybit per-message 1024 is larger than current 512-ID cursor."""
    join = _join()
    delivered = []
    for index in range(1024):
        event = _trade(index)
        delivered.extend(join.accept(event))

    assert [event.exec_id for event in delivered] == [
        f"exec-17-{index}" for index in range(1024)
    ]
    assert not join.pending


def test_same_sequence_can_span_multiple_messages_and_survive_restore() -> None:
    """Do not drop any unique execId or replay duplicates across a checkpoint."""
    join = _join()
    first_frame = [_trade(index) for index in range(1024)]
    next_frame = [_trade(index) for index in range(1024, 1300)]

    delivered = [
        emitted
        for event in first_frame + next_frame
        for emitted in join.accept(event)
    ]
    assert len(delivered) == 1300
    assert [item.exec_id for item in delivered] == [
        item.exec_id for item in first_frame + next_frame
    ]

    restored = P90L90CausalTransportJoin.restore(join.snapshot())
    for duplicate in (first_frame[0], first_frame[511], next_frame[-1]):
        assert restored.accept(duplicate) == ()

    # The sequence may advance; the old sequence cannot be replayed.
    next_sequence = _trade(1300, seq=18)
    assert restored.accept(next_sequence) == (next_sequence,)
    with pytest.raises(RuntimeError, match="regressed before committed sequence"):
        restored.accept(_trade(1301, seq=17))


def test_duplicate_in_pending_queue_remains_exactly_once() -> None:
    """Guard the existing withheld 5m/15m causal-bar contract."""
    previous_bar = START - timedelta(minutes=15)
    join = P90L90CausalTransportJoin(
        symbol=SYMBOL,
        closed_5m_at=START - timedelta(minutes=5),
        closed_15m_at=previous_bar,
    )
    event = _trade(1)
    assert join.accept(event) == ()
    assert join.accept(event) == ()
    assert join.pending == [event]
    assert P90L90CausalTransportJoin.restore(join.snapshot()).pending == [event]
