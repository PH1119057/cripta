"""Boundary race/restart contract for the P90L90 no-order causal join.

This tests out-of-order publicTrade and kline delivery, not actual production
DB durability. No StrategyActivation, Bybit order or file writes.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
    P90TransportEvent,
)

B0 = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)


def _trade(seq: int, seconds: int, name: str = "LTCUSDT") -> P90TransportEvent:
    return P90TransportEvent(
        event_id=f"trade-{seq}", symbol=name, kind="PUBLIC_TRADE",
        event_at=B0+timedelta(seconds=seconds),
        source_json=f'{{"exec_id":"e{seq}","price":"100"}}',
        exchange_sequence=seq, exec_id=f"e{seq}",
    )


def _bar(interval: str, minutes: int, name="LTCUSDT") -> P90TransportEvent:
    t = B0+timedelta(minutes=minutes)
    return P90TransportEvent(
        event_id=f"bar-{interval}-{minutes}", symbol=name,
        kind="CANDLE_CLOSED", timeframe=interval,
        event_at=t, source_json='{"closed":true}',
    )


def _gate() -> P90L90CausalTransportJoin:
    return P90L90CausalTransportJoin(
        symbol="LTCUSDT",
        closed_5m_at=B0-timedelta(minutes=5),
        closed_15m_at=B0-timedelta(minutes=15),
    )


def test_first_native_trade_waits_for_both_5m_and_15m_bar():
    gate = _gate()
    assert gate.accept(_trade(10, 1)) == ()
    assert gate.accept(_bar("5", 0)) == (_bar("5", 0),)
    # First trade cannot be used for Entry or Exit without 15m 05:00.
    assert gate.pending == [_trade(10, 1)]
    assert gate.accept(_bar("5", 5)) == ()
    assert gate.pending == [_trade(10, 1), _bar("5", 5)]
    # The delayed 15m frame causes the 05:00 trade to be delivered BEFORE
    # 05:05 bar. No use of a later completed candle for earlier trade.
    released = gate.accept(_bar("15", 0))
    assert released == (_bar("15", 0), _trade(10, 1), _bar("5", 5))
    assert gate.pending == []
    assert gate.closed_5m_at == B0+timedelta(minutes=5)
    assert gate.closed_15m_at == B0


def test_two_native_buckets_preserve_cursor_and_restart_pending_payload():
    gate = _gate()
    assert gate.accept(_trade(10, 1)) == ()
    assert gate.accept(_trade(11, 301)) == ()
    payload = gate.snapshot()
    restarted = P90L90CausalTransportJoin.restore(payload)
    assert restarted.snapshot() == payload
    # Receive 5m bars in reverse order; older native trade cannot see the
    # future bar and the gap is detected until its actual candle is supplied.
    with pytest.raises(RuntimeError, match="missing/noncausal completed 5m"):
        restarted.accept(_bar("5", 5))
    # A failed transaction must roll back the snapshot at the caller.
    restarted = P90L90CausalTransportJoin.restore(payload)
    assert restarted.accept(_bar("5", 0)) == (_bar("5", 0),)
    assert restarted.accept(_bar("15", 0)) == (
        _bar("15", 0), _trade(10, 1),
    )
    assert restarted.accept(_bar("5", 5)) == (
        _bar("5", 5), _trade(11, 301),
    )
    assert restarted.pending == []


def test_no_cross_symbol_trade_and_no_stale_earlier_sequence():
    gate = _gate()
    with pytest.raises(ValueError, match="cross-symbol"):
        gate.accept(_trade(1, 1, "DOTUSDT"))
    assert gate.accept(_bar("5", 0)) == (_bar("5", 0),)
    assert gate.accept(_bar("15", 0)) == (_bar("15", 0),)
    assert gate.accept(_trade(4, 1)) == (_trade(4, 1),)
    with pytest.raises(RuntimeError, match="sequence regressed"):
        gate.accept(_trade(3, 2))
    with pytest.raises(RuntimeError, match="time regressed"):
        gate.accept(_trade(5, 0))


def test_unresolved_boundary_snapshot_is_not_silent_skip():
    gate = _gate()
    assert gate.accept(_trade(5, 1)) == ()
    assert len(gate.snapshot()["pending"]) == 1
    with pytest.raises(ValueError, match="checkpoint schema unsupported"):
        P90L90CausalTransportJoin.restore({
            **gate.snapshot(), "schema": "UNKNOWN",
        })
    with pytest.raises(ValueError, match="requires UTC"):
        P90L90CausalTransportJoin.restore({
            **gate.snapshot(),
            "last_trade_event_at": "2026-10-28T05:00:01",
        })
