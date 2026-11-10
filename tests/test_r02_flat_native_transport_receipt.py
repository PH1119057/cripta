"""R02: passive original native PG transport must work while P90 is flat.

There is no Exchange, Strategy, Exit or P90 MFE grant from these tests.
The existing OPEN-repair consumer keeps its original strict gate.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_workbench.universal_entry.p90l90_scoped_native_source import (
    OrderedNativeTrade,
)
from operations.monitoring.native_public_trade_observer_delivery import (
    NativeObserverDeliveryBlocked,
    validate_technical_native_receipt,
)


def _event() -> OrderedNativeTrade:
    event_at = datetime(2026, 11, 10, 22, 0, tzinfo=UTC)
    return OrderedNativeTrade(
        frame_number=24,
        frame_index=0,
        symbol="APTUSDT",
        exchange_sequence=1234567,
        exec_id="r02-transport-test-original-execid",
        event_at=event_at,
        price=Decimal("1.77"),
        quantity=Decimal("11"),
        taker_side="Buy",
        db_accepted_at=event_at + timedelta(milliseconds=40),
    )


def test_passive_native_receipt_validates_original_and_returns_no_signal() -> None:
    assert validate_technical_native_receipt(_event()) is None


@pytest.mark.parametrize("invalid", [
    {"exec_id": ""},
    {"exchange_sequence": -1},
    {"db_accepted_at": datetime(2026, 11, 10, 21, 59, 0, tzinfo=UTC)},
])
def test_passive_invalid_native_receipt_cannot_be_acknowledged(
    invalid: dict[str, object],
) -> None:
    with pytest.raises(
        NativeObserverDeliveryBlocked,
        match="NATIVE_OBSERVER_TECHNICAL_ORIGINAL_ROW_UNVERIFIED",
    ):
        validate_technical_native_receipt(replace(_event(), **invalid))


def test_r02_transport_ack_runs_in_flat_observer_not_only_open_repair() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "operations" / "monitoring" / "universal_entry_shadow.py"
    )
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path))
    assert (
        "native_pg_transport_probe = (\n"
        "        EXTERNAL_NATIVE_SOURCE and bool(observer_split.p90_real)\n"
        "    )"
    ) in source
    block = source.split("            if native_pg_transport_probe:\n", 1)[1]
    block = block.split("            message: dict[str, Any] | None = None", 1)[0]
    assert "on_trade=(" in block
    assert "if native_pg_delivery_only" in block
    assert "else validate_technical_native_receipt" in block
    assert "TECHNICAL_CATCHING_UP" in block
    assert "TECHNICAL_CAUGHT_UP" in block
    assert "native_pg_delivery_only\n" in block
    assert "trade_cursors.clear()" in block
    assert (
        "native_pg_delivery_scope" in source
        and "PASSIVE_ORIGINAL_TRANSPORT_ONLY" in source
    )
    # Existing Strategy-owned WS path remains, with no premature R06 cutover.
    assert "elif topic.startswith(\"publicTrade.\"):" in source
    assert "NATIVE_SOURCE_EXTERNAL_EXACT_EVENT_NOT_COMMITTED" in source
