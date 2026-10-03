from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from bybit_workbench.market_observation_alert import (
    ALERT_CLASSES,
    acknowledge_alert_delivery,
    create_market_observation_alert,
    process_due_alert_deliveries,
)

NOW = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)


@dataclass
class FakeCursor:
    rowcount: int = 0
    one: Any = None
    all_rows: tuple[Any, ...] = ()

    def fetchone(self) -> Any:
        return self.one

    def fetchall(self) -> tuple[Any, ...]:
        return self.all_rows


class ScriptedConnection:
    def __init__(self, cursors: list[FakeCursor]) -> None:
        self._cursors = list(cursors)
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> FakeCursor:
        self.calls.append((statement, tuple(parameters)))
        if not self._cursors:
            raise AssertionError(f"unexpected SQL: {statement}")
        return self._cursors.pop(0)


def _provenance() -> dict[str, object]:
    return {
        "trading_command": False,
        "source": "controlled-test",
        "schema_version": "market-observation-alert-v1",
    }


def test_alert_classes_match_canonical_minimum() -> None:
    assert {
        "REGIME_CHANGE",
        "MARKET_WIDE_STRESS",
        "SYNCHRONIZATION_SPIKE",
        "LIQUIDATION_CASCADE",
        "DATA_QUALITY_DEGRADATION",
        "SOURCE_OUTAGE",
    } == ALERT_CLASSES


def test_create_alert_is_deterministic_and_non_notifiable_by_default() -> None:
    connection = ScriptedConnection([FakeCursor(rowcount=1)])
    first = create_market_observation_alert(
        connection,
        alert_class="SOURCE_OUTAGE",
        observed_at=NOW,
        source_component="MAYAK",
        scope_key="source:bybit-public-trades",
        payload={"status": "NO_DATA"},
        provenance=_provenance(),
    )

    assert first.alert_id.startswith("market-alert-")
    assert first.owner_notifiable is False
    assert len(connection.calls) == 1
    assert "market_observation_alerts" in connection.calls[0][0]
    assert "market_observation_alert_deliveries" not in connection.calls[0][0]

    second_connection = ScriptedConnection([FakeCursor(rowcount=0)])
    second = create_market_observation_alert(
        second_connection,
        alert_class="SOURCE_OUTAGE",
        observed_at=NOW,
        source_component="MAYAK",
        scope_key="source:bybit-public-trades",
        payload={"status": "NO_DATA"},
        provenance=_provenance(),
    )
    assert second.alert_id == first.alert_id


def test_owner_notifiable_alert_creates_delivery_separately() -> None:
    connection = ScriptedConnection([FakeCursor(rowcount=1), FakeCursor(rowcount=1)])
    alert = create_market_observation_alert(
        connection,
        alert_class="DATA_QUALITY_DEGRADATION",
        observed_at=NOW,
        source_component="MAYAK",
        scope_key="quality:liquidations",
        payload={"quality": "INSUFFICIENT"},
        provenance=_provenance(),
        owner_notifiable=True,
    )

    assert alert.owner_notifiable is True
    assert len(connection.calls) == 2
    delivery_sql, delivery_params = connection.calls[1]
    assert "market_observation_alert_deliveries" in delivery_sql
    assert delivery_params[1] == alert.alert_id
    assert "OWNER_WEBHOOK" in delivery_sql


def test_alert_creation_rejects_hidden_trading_semantics() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        create_market_observation_alert(
            ScriptedConnection([]),
            alert_class="ALLOW_ENTRY",
            observed_at=NOW,
            source_component="MAYAK",
            scope_key="global",
            payload={},
            provenance=_provenance(),
        )

    with pytest.raises(ValueError, match="trading_command"):
        create_market_observation_alert(
            ScriptedConnection([]),
            alert_class="SOURCE_OUTAGE",
            observed_at=NOW,
            source_component="MAYAK",
            scope_key="global",
            payload={},
            provenance={"trading_command": True},
        )


def test_delivery_success_stays_separate_from_acknowledgement() -> None:
    payload = {
        "delivery_id": "market-alert-delivery-1",
        "alert_id": "market-alert-1",
    }
    connection = ScriptedConnection(
        [
            FakeCursor(rowcount=0),
            FakeCursor(
                all_rows=(
                    {
                        "delivery_id": "market-alert-delivery-1",
                        "alert_id": "market-alert-1",
                        "payload": payload,
                        "attempts": 0,
                    },
                )
            ),
            FakeCursor(rowcount=1),
            FakeCursor(one=(1,)),
        ]
    )
    sent: list[dict[str, object]] = []

    result = process_due_alert_deliveries(
        connection,
        now=NOW,
        sender=lambda item: sent.append(dict(item)),
        max_attempts=3,
        retry_seconds=30,
        acknowledgement_timeout_seconds=300,
    )

    assert result.attempted == 1
    assert result.delivered == 1
    assert result.escalated == 0
    assert result.pending == 1
    assert sent == [payload]
    assert "SET state='DELIVERED'" in connection.calls[2][0]


def test_missing_sender_is_explicit_failure_not_delivery() -> None:
    connection = ScriptedConnection(
        [
            FakeCursor(rowcount=0),
            FakeCursor(
                all_rows=(
                    {
                        "delivery_id": "market-alert-delivery-2",
                        "alert_id": "market-alert-2",
                        "payload": {"alert_id": "market-alert-2"},
                        "attempts": 0,
                    },
                )
            ),
            FakeCursor(one=(0,)),
            FakeCursor(rowcount=1),
            FakeCursor(one=(1,)),
        ]
    )

    result = process_due_alert_deliveries(
        connection,
        now=NOW,
        sender=None,
        max_attempts=3,
        retry_seconds=30,
        acknowledgement_timeout_seconds=300,
    )

    assert result.attempted == 1
    assert result.delivered == 0
    assert any(
        "OWNER_WEBHOOK_NOT_CONFIGURED" in str(parameters)
        for _, parameters in connection.calls
    )


def test_acknowledgement_is_explicit_transition() -> None:
    connection = ScriptedConnection([FakeCursor(rowcount=1)])
    assert acknowledge_alert_delivery(
        connection,
        delivery_id="market-alert-delivery-3",
        acknowledged_at=NOW,
    )
    assert "ACKNOWLEDGED" in connection.calls[0][0]


def test_sql_contract_is_fail_closed_and_non_trading() -> None:
    sql = (
        Path(__file__).parents[1]
        / "operations"
        / "sql"
        / "20261003_market_observation_alert_v1.sql"
    ).read_text(encoding="utf-8")
    for token in ALERT_CLASSES:
        assert f"'{token}'" in sql
    assert "owner_notifiable BOOLEAN NOT NULL DEFAULT FALSE" in sql
    assert "runtime.reject_immutable_change()" in sql
    assert "REVOKE DELETE" in sql
    assert "trading_command" in sql
