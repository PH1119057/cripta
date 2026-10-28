"""REAL native callback cannot bypass a durable SECOND48 state owner."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.contracts import FrozenPolicy, MarketFactEnvelope
from bybit_workbench.universal_entry.p90l90_observer_real_consumer import P90L90ObserverRealConsumer
from bybit_workbench.universal_entry.transport_continuity import TradeCursor


AT = datetime(2026, 10, 29, 2, 0, tzinfo=UTC)


def test_real_consumer_refuses_missing_durable_embargo_before_any_entry_callback() -> None:
    owner = SimpleNamespace()
    calls: list[object] = []
    owner.accept = lambda *args, **kwargs: calls.append((args, kwargs))
    consumer = object.__new__(P90L90ObserverRealConsumer)
    consumer._candidate_owners = {"LTCUSDT": owner}
    consumer._admission_provider = lambda _symbol, _at: ((), True, False, True)

    fact = MarketFactEnvelope(
        fact_id="synthetic-fact",
        event_kind="P90L90_RAW_H9_PREV7D_CROSSING",
        symbol="LTCUSDT",
        event_at=AT,
        observed_at=AT,
        received_at=AT,
        source_refs=("test:synthetic:no-exchange",),
        attributes=FrozenPolicy.from_mapping({}),
    )
    cursor = TradeCursor(symbol="LTCUSDT", exec_id="test-exec", seq=1, traded_at=AT)
    with pytest.raises(RuntimeError, match="SECOND48_DURABLE_STATE_NOT_VERIFIED"):
        consumer._on_candidate(fact, cursor)
    assert calls == []


def test_proven_second48_pause_is_not_converted_into_allow_new_signals() -> None:
    calls: list[dict[str, object]] = []

    class _FakeOwner:
        def accept(self, *_args: object, **kwargs: object) -> None:
            calls.append(kwargs)

    consumer = object.__new__(P90L90ObserverRealConsumer)
    consumer._candidate_owners = {"LTCUSDT": _FakeOwner()}
    consumer._admission_provider = lambda _symbol, _at: ((), True, True, False)
    fact = MarketFactEnvelope(
        fact_id="synthetic-fact-2",
        event_kind="P90L90_RAW_H9_PREV7D_CROSSING",
        symbol="LTCUSDT",
        event_at=AT, observed_at=AT, received_at=AT,
        source_refs=("test:synthetic:no-exchange",),
        attributes=FrozenPolicy.from_mapping({}),
    )
    cursor = TradeCursor(symbol="LTCUSDT", exec_id="test-exec2", seq=2, traded_at=AT)
    consumer._on_candidate(fact, cursor)
    assert len(calls) == 1
    assert calls[0]["allow_new_signals"] is False
