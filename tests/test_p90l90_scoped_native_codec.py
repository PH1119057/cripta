"""#470: original provider trade identity survives PostgreSQL-only consumption."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.universal_entry.p90l90_scoped_native_codec import (
    original_native_fact,
)
from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    NativeSourceProofUnavailable,
    OrderedNativeTrade,
)


def _event() -> OrderedNativeTrade:
    traded = datetime(2026, 11, 8, 19, 45, 1, 203000, UTC)
    return OrderedNativeTrade(
        frame_number=14, frame_index=2, symbol="LTCUSDT",
        exchange_sequence=9021, exec_id="provider-seq-9021-trade2",
        event_at=traded, price=Decimal("52.123"),
        quantity=Decimal("0.2"), taker_side="Sell",
        db_accepted_at=traded + timedelta(milliseconds=71),
    )


def test_original_trade_identity_and_time_are_preserved_for_engine() -> None:
    source = _event()
    fact, cursor = original_native_fact(source)
    assert fact.fact_id == "trade-" + source.exec_id
    assert fact.event_kind == "PUBLIC_TRADE"
    assert fact.symbol == cursor.symbol == source.symbol
    assert fact.event_at == fact.observed_at == cursor.traded_at == source.event_at
    assert fact.received_at == source.db_accepted_at
    assert fact.source_refs == (
        "bybit:publicTrade:LTCUSDT:provider-seq-9021-trade2",
    )
    assert fact.attributes.to_dict() == {
        "price": "52.123", "size": "0.2", "taker_side": "Sell",
    }
    assert cursor.exec_id == source.exec_id
    assert cursor.seq == source.exchange_sequence


def test_replay_is_never_relabelled_as_a_current_trade() -> None:
    old = _event()
    fact, _ = original_native_fact(old)
    assert fact.event_at.year == 2026
    assert fact.received_at < datetime(2026, 11, 9, tzinfo=UTC)
    assert fact.received_at != datetime.now(UTC)


def test_missing_native_source_clock_is_rejected() -> None:
    old = _event()
    for drift in (
        replace(old, db_accepted_at=old.event_at - timedelta(minutes=4)),
        replace(old, db_accepted_at=old.event_at.replace(tzinfo=None)),
        replace(old, exchange_sequence=-1),
        replace(old, exec_id=""),
    ):
        with pytest.raises(
            NativeSourceProofUnavailable,
            match="ORIGINAL_TRADE_ENVELOPE_INVALID",
        ):
            original_native_fact(drift)
