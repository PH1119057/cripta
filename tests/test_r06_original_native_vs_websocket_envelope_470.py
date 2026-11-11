"""R06 frozen original provider trade parity before Entry consumer cutover.

The exact original provider identity and causal trade fields must survive
WS -> immutable PG. WS receive time and PG accept time are DIFFERENT provenance;
we never fake their equality, and this test authorizes no trading.
"""
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
from operations.monitoring.universal_entry_shadow import (
    _trade_cursor,
    _trade_fact,
)

WHEN = datetime(2026, 11, 11, 18, 46, 11, 123000, tzinfo=UTC)


@pytest.mark.parametrize(
    ("symbol", "price", "quantity", "side", "exec_id", "seq"),
    [
        ("APTUSDT", "3.5500", "0.7000", "Buy", "bybit-apt-1", 12),
        ("ARBUSDT", "0.9123", "123.00", "Sell", "bybit-arb-2", 12),
        ("DOTUSDT", "4.320", "0.1", "Buy", "bybit-dot-3", 13),
        ("INJUSDT", "9.75", "21.00", "Sell", "bybit-inj-4", 13),
        ("LTCUSDT", "75.0100", "0.045", "Buy", "bybit-ltc-5", 14),
    ],
)
def test_exact_original_identity_cursor_and_geometry_fields_match(
    symbol: str, price: str, quantity: str,
    side: str, exec_id: str, seq: int,
) -> None:
    raw = {
        "s": symbol, "p": price, "v": quantity, "S": side,
        "i": exec_id, "seq": seq,
        "T": int(WHEN.timestamp() * 1000),
    }
    # Distinct observed transports are never conflated. Comparing both
    # with equal receive time is only a deterministic field normalization.
    accepted = WHEN + timedelta(milliseconds=180)
    event = OrderedNativeTrade(
        frame_number=10, frame_index=1, symbol=symbol,
        exchange_sequence=seq, exec_id=exec_id, event_at=WHEN,
        price=Decimal(price), quantity=Decimal(quantity),
        taker_side=side, db_accepted_at=accepted,
    )
    old_fact = _trade_fact(raw, accepted)
    old_cursor = _trade_cursor(raw)
    pinned_fact, pinned_cursor = original_native_fact(event)
    assert old_fact.fact_id == pinned_fact.fact_id
    assert old_fact.symbol == pinned_fact.symbol
    assert old_fact.observed_at == pinned_fact.observed_at
    assert old_fact.event_at == pinned_fact.event_at
    assert old_fact.source_refs == pinned_fact.source_refs
    assert old_fact.attributes.to_dict() == pinned_fact.attributes.to_dict()
    assert old_cursor == pinned_cursor


def test_db_accept_time_is_not_falsely_called_original_ws_receive() -> None:
    received = WHEN + timedelta(milliseconds=80)
    accepted = WHEN + timedelta(milliseconds=210)
    raw = {
        "s": "LTCUSDT", "p": "75.1", "v": "0.5", "S": "Buy",
        "i": "different-transport-times", "seq": 25,
        "T": int(WHEN.timestamp() * 1000),
    }
    event = OrderedNativeTrade(
        frame_number=12, frame_index=0, symbol="LTCUSDT",
        exchange_sequence=25, exec_id="different-transport-times",
        event_at=WHEN, price=Decimal("75.1"), quantity=Decimal("0.5"),
        taker_side="Buy", db_accepted_at=accepted,
    )
    old_fact = _trade_fact(raw, received)
    pinned_fact, pinned_cursor = original_native_fact(event)
    assert old_fact.received_at == received
    assert pinned_fact.received_at == accepted
    assert pinned_fact.received_at != old_fact.received_at
    assert old_fact.event_at == pinned_fact.event_at
    assert _trade_cursor(raw) == pinned_cursor


def test_pg_decoder_rejects_missing_real_provider_identity() -> None:
    bad = OrderedNativeTrade(
        frame_number=1, frame_index=0, symbol="LTCUSDT",
        exchange_sequence=10, exec_id="", event_at=WHEN,
        price=Decimal("50"), quantity=Decimal("1"),
        taker_side="Buy", db_accepted_at=WHEN,
    )
    with pytest.raises(
        NativeSourceProofUnavailable, match="ORIGINAL_TRADE_ENVELOPE_INVALID",
    ):
        original_native_fact(bad)
