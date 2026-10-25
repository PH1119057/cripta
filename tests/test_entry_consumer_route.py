from __future__ import annotations

from unittest.mock import patch

import pytest

import operations.connectivity.universal_entry_consumer as consumer
from bybit_workbench.entry_consumer_route import (
    ConsumerWorkerRoute,
    check_consumer_worker_route,
)


class Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows

    def fetchone(self) -> None:
        return None


class SafetyRows:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row

    def fetchall(self) -> list[tuple[object, ...]]:
        return [] if self.row is None else [self.row]


class FakeDB:
    def __init__(
        self, rows: list[dict[str, object]], *, blocked: bool = False,
        schema: bool = True,
    ) -> None:
        self.rows = rows
        self.blocked = blocked
        self.schema = schema
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(
        self, sql: str, args: tuple[object, ...] = ()
    ) -> Rows | SafetyRows:
        self.queries.append((sql, args))
        if "to_regclass" in sql:
            return SafetyRows(("runtime.account_entry_safety_blocks",) if self.schema else (None,))
        if "FROM runtime.account_entry_safety_blocks" in sql:
            return SafetyRows((1,) if self.blocked else None)
        return Rows(self.rows)


def identity(account: str = "ta-one", connector: str = "connector-one") -> dict[str, object]:
    return {
        "trading_account_id": account,
        "connector_account_id": account,
        "account_connector_id": connector,
        "venue_id": "venue-bybit",
        "environment": "MAINNET",
        "status": "ACTIVE",
        "account_type": "UNIFIED",
        "account_kind": "MAIN",
        "external_account_ref": "123456",
        "connector_kind": "CEX_PRIVATE_RUNTIME",
        "adapter_id": "BYBIT_V5_PRIVATE",
        "credential_ref": "credential-ref-only",
        "connector_status": "ACTIVE",
        "retired_at": None,
    }


def test_registered_route_exactly_identifies_worker_trading_account_and_connector() -> None:
    db = FakeDB([identity()])
    result = check_consumer_worker_route(
        db, route=ConsumerWorkerRoute("ta-one", "connector-one")
    )
    assert result.ready
    assert not result.reasons
    assert len(db.queries) == 1
    sql, args = db.queries[0]
    assert args == ("ta-one", "connector-one")
    assert "FROM identity.trading_accounts a" in sql
    assert "JOIN identity.account_connectors c" in sql
    assert "a.trading_account_id=%s AND c.account_connector_id=%s" in sql
    assert not any(word in sql for word in ("UPDATE ", "INSERT ", "DELETE "))


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"connector_account_id": "ta-other"}, "ACCOUNT_CONNECTOR_ROUTE_IDENTITY_MISMATCH"),
        ({"environment": "TESTNET"}, "TRADING_ACCOUNT_ROUTE_NOT_ACTIVE"),
        ({"status": "INACTIVE"}, "TRADING_ACCOUNT_ROUTE_NOT_ACTIVE"),
        ({"account_type": "CLASSIC"}, "TRADING_ACCOUNT_ROUTE_NOT_ACTIVE"),
        ({"external_account_ref": None}, "TRADING_ACCOUNT_REGISTERED_UID_MISSING"),
        ({"external_account_ref": "unknown"}, "TRADING_ACCOUNT_REGISTERED_UID_MISSING"),
        ({"credential_ref": None}, "ACCOUNT_PRIVATE_CONNECTOR_NOT_ACTIVE"),
        ({"adapter_id": "OTHER"}, "ACCOUNT_PRIVATE_CONNECTOR_NOT_ACTIVE"),
        ({"retired_at": "expired"}, "ACCOUNT_PRIVATE_CONNECTOR_NOT_ACTIVE"),
    ],
)
def test_invalid_route_fails_closed(change: dict[str, object], expected: str) -> None:
    db = FakeDB([{**identity(), **change}])
    result = check_consumer_worker_route(
        db, route=ConsumerWorkerRoute("ta-one", "connector-one")
    )
    assert not result.ready
    assert expected in result.reasons


@pytest.mark.parametrize("rows", [[], [identity(), identity()]])
def test_missing_or_ambiguous_registered_route_fails_closed(
    rows: list[dict[str, object]],
) -> None:
    result = check_consumer_worker_route(
        FakeDB(rows), route=ConsumerWorkerRoute("ta-one", "connector-one")
    )
    assert not result.ready
    assert result.reasons == ("ACCOUNT_CONNECTOR_ROUTE_NOT_UNIQUE",)


def test_consumer_routes_different_private_workers_to_separate_request_partitions() -> None:
    for account, connector in (("ta-one", "connector-one"), ("ta-two", "connector-two")):
        db = FakeDB([identity(account, connector)])
        with (
            patch.object(consumer, "TRADING_ACCOUNT_ID", account),
            patch.object(consumer, "ACCOUNT_CONNECTOR_ID", connector),
            patch.object(consumer, "_release_expired_reserved_requests", return_value=0),
            patch.object(consumer, "_execution_gate_enabled", return_value=True),
        ):
            outcome = consumer.run_once(db)
        assert outcome == "NO_REQUEST"
        assert db.queries[0][1] == (account, connector)
        assert "to_regclass" in db.queries[1][0]
        assert db.queries[2][1] == (account, connector)
        query, params = db.queries[3]
        assert "r.trading_account_id=%s" in query
        assert params == (account,)


def test_consumer_route_rejection_prevents_query_or_dispatch() -> None:
    db = FakeDB([identity("ta-two", "connector-two")])
    with (
        patch.object(consumer, "TRADING_ACCOUNT_ID", "ta-one"),
        patch.object(consumer, "ACCOUNT_CONNECTOR_ID", "connector-one"),
        patch.object(consumer, "_release_expired_reserved_requests", return_value=0),
        patch.object(consumer, "_execution_gate_enabled", return_value=True),
    ):
        outcome = consumer.run_once(db)
    assert outcome == "ACCOUNT_CONNECTOR_ROUTE_NOT_READY:ACCOUNT_CONNECTOR_ROUTE_IDENTITY_MISMATCH"
    assert len(db.queries) == 1


def test_consumer_cannot_impersonate_second_worker_through_route_argument() -> None:
    db = FakeDB([identity()])
    with (
        patch.object(consumer, "TRADING_ACCOUNT_ID", "ta-one"),
        patch.object(consumer, "ACCOUNT_CONNECTOR_ID", "connector-one"),
        pytest.raises(ValueError, match="CONSUMER_ROUTE_PROCESS_IDENTITY_MISMATCH"),
    ):
        consumer._next_request(db, route=ConsumerWorkerRoute("ta-two", "connector-two"))
    assert not db.queries


def test_consumer_route_ids_are_required() -> None:
    with pytest.raises(ValueError, match="CONSUMER_WORKER_ROUTE_IDENTITY_MISSING"):
        ConsumerWorkerRoute("", "connector")
    with pytest.raises(ValueError, match="CONSUMER_WORKER_ROUTE_IDENTITY_MISSING"):
        ConsumerWorkerRoute("account", "")


@pytest.mark.parametrize(
    ("blocked", "schema", "reason"),
    [
        (True, True, "ACCOUNT_ENTRY_SAFETY_BLOCKED"),
        (False, False, "ACCOUNT_ENTRY_SAFETY_SCHEMA_MISSING"),
    ],
)
def test_account_latch_denies_new_request_before_any_queue_claim(
    blocked: bool, schema: bool, reason: str,
) -> None:
    db = FakeDB([identity()], blocked=blocked, schema=schema)
    with (
        patch.object(consumer, "TRADING_ACCOUNT_ID", "ta-one"),
        patch.object(consumer, "ACCOUNT_CONNECTOR_ID", "connector-one"),
        patch.object(consumer, "_release_expired_reserved_requests", return_value=0),
        patch.object(consumer, "_execution_gate_enabled", return_value=True),
    ):
        result = consumer.run_once(db)
    assert result == "ACCOUNT_ENTRY_SAFETY_NOT_READY:" + reason
    assert not any("FROM strategy_entry.execution_requests" in x for x, _ in db.queries)
