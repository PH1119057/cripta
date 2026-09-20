from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.fault_delivery import (
    acknowledge_delivery,
    process_due_deliveries,
)

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not DSN,
    reason="set CRIPTA_TRADE_LIFECYCLE_TEST_DSN to an explicitly disposable PostgreSQL DB",
)

NOW = datetime(2026, 9, 20, 11, 0, tzinfo=UTC)


def _seed_delivery(prefix: str, *, state: str = "PENDING", attempts: int = 0) -> str:
    assert DSN is not None
    fault_id = f"{prefix}-fault"
    delivery_id = f"{prefix}-delivery"
    delivered_at = NOW if state in {"DELIVERED", "ACKNOWLEDGED", "ESCALATION_REQUIRED"} else None
    with psycopg.connect(DSN) as connection:
        connection.execute(
            """INSERT INTO runtime.lifecycle_faults(
                   fault_id,fault_code,severity,state,strategy_position_id,
                   detected_at,resolved_at,exact_ids,payload
               ) VALUES(
                   %s,'EXCHANGE_POSITION_OWNERSHIP_INVARIANT_BROKEN',
                   'CRITICAL','OPEN',NULL,%s,NULL,'{}'::jsonb,'{}'::jsonb
               )""",
            (fault_id, NOW),
        )
        connection.execute(
            """INSERT INTO runtime.lifecycle_fault_deliveries(
                   delivery_id,fault_id,channel,state,attempts,next_attempt_at,
                   last_attempt_at,delivered_at,acknowledged_at,escalation_at,
                   last_error,payload
               ) VALUES(
                   %s,%s,'OWNER_WEBHOOK',%s,%s,%s,NULL,%s,NULL,NULL,NULL,
                   %s::jsonb
               )""",
            (
                delivery_id,
                fault_id,
                state,
                attempts,
                NOW,
                delivered_at,
                '{"delivery_id":"' + delivery_id + '","fault_id":"' + fault_id + '"}',
            ),
        )
    return delivery_id


def test_successful_delivery_requires_separate_owner_ack() -> None:
    assert DSN is not None
    prefix = "delivery-ok-" + uuid4().hex
    delivery_id = _seed_delivery(prefix)
    sent: list[dict[str, object]] = []

    with psycopg.connect(DSN) as connection:
        result = process_due_deliveries(
            connection,
            now=NOW,
            sender=lambda payload: sent.append(dict(payload)),
            max_attempts=3,
            retry_seconds=30,
            acknowledgement_timeout_seconds=300,
        )
        row = connection.execute(
            """SELECT state,attempts,delivered_at,acknowledged_at
                 FROM runtime.lifecycle_fault_deliveries
                WHERE delivery_id=%s""",
            (delivery_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "DELIVERED"
        assert row[1] == 1
        assert row[2] is not None
        assert row[3] is None
        assert acknowledge_delivery(
            connection,
            delivery_id=delivery_id,
            acknowledged_at=NOW + timedelta(seconds=5),
        )
        state = connection.execute(
            "SELECT state FROM runtime.lifecycle_fault_deliveries WHERE delivery_id=%s",
            (delivery_id,),
        ).fetchone()[0]

    assert result.attempted >= 1
    assert result.delivered >= 1
    assert any(item.get("delivery_id") == delivery_id for item in sent)
    assert state == "ACKNOWLEDGED"


def test_delivery_failure_retries_then_enters_explicit_escalation() -> None:
    assert DSN is not None
    prefix = "delivery-fail-" + uuid4().hex
    delivery_id = _seed_delivery(prefix)

    def fail(_payload: dict[str, object]) -> None:
        raise RuntimeError("test transport down")

    with psycopg.connect(DSN) as connection:
        first = process_due_deliveries(
            connection,
            now=NOW,
            sender=fail,
            max_attempts=2,
            retry_seconds=30,
            acknowledgement_timeout_seconds=300,
        )
        row = connection.execute(
            """SELECT state,attempts,next_attempt_at,last_error
                 FROM runtime.lifecycle_fault_deliveries WHERE delivery_id=%s""",
            (delivery_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "PENDING"
        assert row[1] == 1
        assert row[2] == NOW + timedelta(seconds=30)
        assert "test transport down" in row[3]

        second = process_due_deliveries(
            connection,
            now=NOW + timedelta(seconds=30),
            sender=fail,
            max_attempts=2,
            retry_seconds=30,
            acknowledgement_timeout_seconds=300,
        )
        row = connection.execute(
            """SELECT state,attempts,escalation_at,last_error
                 FROM runtime.lifecycle_fault_deliveries WHERE delivery_id=%s""",
            (delivery_id,),
        ).fetchone()

    assert first.attempted == 1
    assert second.attempted == 1
    assert row is not None
    assert row[0] == "ESCALATION_REQUIRED"
    assert row[1] == 2
    assert row[2] == NOW + timedelta(seconds=30)


def test_delivered_but_unacknowledged_fault_escalates_after_timeout() -> None:
    assert DSN is not None
    prefix = "delivery-ack-timeout-" + uuid4().hex
    delivery_id = _seed_delivery(prefix, state="DELIVERED", attempts=1)

    with psycopg.connect(DSN) as connection:
        result = process_due_deliveries(
            connection,
            now=NOW + timedelta(seconds=301),
            sender=lambda _payload: None,
            max_attempts=3,
            retry_seconds=30,
            acknowledgement_timeout_seconds=300,
        )
        row = connection.execute(
            """SELECT state,escalation_at,last_error
                 FROM runtime.lifecycle_fault_deliveries WHERE delivery_id=%s""",
            (delivery_id,),
        ).fetchone()

    assert result.escalated >= 1
    assert row is not None
    assert row[0] == "ESCALATION_REQUIRED"
    assert row[1] == NOW + timedelta(seconds=301)
    assert row[2] == "OWNER_ACK_TIMEOUT"


def test_missing_sender_is_not_silently_counted_as_delivery() -> None:
    assert DSN is not None
    prefix = "delivery-unconfigured-" + uuid4().hex
    delivery_id = _seed_delivery(prefix)

    with psycopg.connect(DSN) as connection:
        result = process_due_deliveries(
            connection,
            now=NOW,
            sender=None,
            max_attempts=3,
            retry_seconds=30,
            acknowledgement_timeout_seconds=300,
        )
        row = connection.execute(
            """SELECT state,attempts,last_error
                 FROM runtime.lifecycle_fault_deliveries WHERE delivery_id=%s""",
            (delivery_id,),
        ).fetchone()

    assert result.attempted == 1
    assert result.delivered == 0
    assert row is not None
    assert row[0] == "PENDING"
    assert row[1] == 1
    assert row[2] == "OWNER_WEBHOOK_NOT_CONFIGURED"
