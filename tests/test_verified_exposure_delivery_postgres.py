"""Disposable-PostgreSQL gate: verified orphaned Strategy claim -> durable delivery.

Run ONLY with CRIPTA_TRADE_LIFECYCLE_TEST_DSN pointing to an explicitly
DISPOSABLE test PostgreSQL database. The test never calls Bybit or a webhook.
It intentionally does not assert Exchange UID proof or production delivery.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from bybit_workbench.fault_delivery import acknowledge_delivery, process_due_deliveries
from bybit_workbench.lifecycle_exposure_bridge import unbound_strategy_claims
from bybit_workbench.lifecycle_supervisor import (
    FaultCondition,
    FaultSeverity,
    LifecycleSupervisor,
)

psycopg = pytest.importorskip("psycopg")
dict_row = pytest.importorskip("psycopg.rows").dict_row

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="requires explicitly disposable CRIPTA_TRADE_LIFECYCLE_TEST_DSN",
)


def _observed_unbound(account: str, symbol: str) -> dict[str, object]:
    return {
        "account_identity_binding": "VERIFIED",  # synthetic test-only provenance
        "trading_account_id": account,
        "symbol": symbol,
        "position_idx": 0,
        "active_strategy_claim_ids": ("test-claim-" + uuid4().hex,),
        "matching_open_owner_ids": (),
        "open_owner_ids": (),
    }


def _condition(account: str, symbol: str) -> FaultCondition:
    observations = (_observed_unbound(account, symbol),)
    unmatched = unbound_strategy_claims(observations, account_id=account)
    assert len(unmatched) == 1
    item = unmatched[0]
    scope = f"claim:{account}:{symbol}:0"
    return FaultCondition(
        "EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN",
        FaultSeverity.CRITICAL,
        scope,
        None,
        {
            "trading_account_id": account,
            "symbol": symbol,
            "position_idx": 0,
            "exchange_position_slot_claim_ids": list(item["exchange_position_slot_claim_ids"]),
        },
        {
            "reason": "physical slot has conflicting or unbound durable ownership",
            "matching_open_owner_ids": list(item["matching_open_owner_ids"]),
        },
    )


def test_unbound_strategy_claim_creates_durable_fault_pending_delivery_and_ack() -> None:
    assert DSN is not None
    suffix = uuid4().hex
    account = f"ta-disposable-{suffix}"
    condition = _condition(account, "UNIUSDT")
    now = datetime.now(UTC)
    sent: list[dict[str, object]] = []

    # A truly disposable database is mandatory: this writes fault and event rows.
    with psycopg.connect(DSN, row_factory=dict_row) as connection:
        supervisor = LifecycleSupervisor(connection, trading_account_id=account)
        fault_id = supervisor._fault_id(condition)
        with connection.transaction():
            assert supervisor._upsert_fault(condition, now) == 1
            assert supervisor._upsert_fault(condition, now) == 0  # no duplicate incident
            fault = connection.execute(
                "SELECT state,severity FROM runtime.lifecycle_faults WHERE fault_id=%s",
                (fault_id,),
            ).fetchone()
            assert fault == {"state": "OPEN", "severity": "CRITICAL"}
            delivery = connection.execute(
                """SELECT delivery_id,state,attempts,acknowledged_at
                     FROM runtime.lifecycle_fault_deliveries WHERE fault_id=%s""",
                (fault_id,),
            ).fetchone()
            assert delivery is not None
            assert delivery["state"] == "PENDING"
            assert delivery["attempts"] == 0
            assert delivery["acknowledged_at"] is None
            delivery_id = delivery["delivery_id"]

        # No real network I/O: sender only captures payloads in this test.
        with connection.transaction():
            result = process_due_deliveries(
                connection,
                now=now + timedelta(seconds=1),
                sender=lambda payload: sent.append(dict(payload)),
                max_attempts=3,
                retry_seconds=30,
                acknowledgement_timeout_seconds=300,
            )
            assert result.delivered >= 1
            delivered = connection.execute(
                """SELECT state,delivered_at,acknowledged_at
                     FROM runtime.lifecycle_fault_deliveries WHERE delivery_id=%s""",
                (delivery_id,),
            ).fetchone()
            assert delivered["state"] == "DELIVERED"
            assert delivered["delivered_at"] is not None
            assert delivered["acknowledged_at"] is None
            assert any(item.get("delivery_id") == delivery_id for item in sent)
            assert acknowledge_delivery(
                connection,
                delivery_id=delivery_id,
                acknowledged_at=now + timedelta(seconds=2),
            )
            acknowledged = connection.execute(
                "SELECT state FROM runtime.lifecycle_fault_deliveries WHERE delivery_id=%s",
                (delivery_id,),
            ).fetchone()
            assert acknowledged["state"] == "ACKNOWLEDGED"


def test_claimless_exchange_position_is_not_strategy_ownership_fault() -> None:
    # No DB needed for negative attribution: this is a pure helper check.
    uidless = {
        "account_identity_binding": "VERIFIED",
        "trading_account_id": "ta-disposable-test",
        "symbol": "BTCUSDT",
        "position_idx": 0,
        "active_strategy_claim_ids": (),
        "matching_open_owner_ids": (),
        "open_owner_ids": (),
    }
    assert unbound_strategy_claims((uidless,), account_id="ta-disposable-test") == ()
