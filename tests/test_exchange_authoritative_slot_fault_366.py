"""Fail-closed Exchange-authoritative resolution of an owner-closed physical slot.

This suite does not connect to production PostgreSQL or Bybit.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

ACCOUNT = "ta-2122c81ecff14f3b85b102db605212f7"
CLAIM = "slot-6447a49fd4dc55a130a0155ac39d9032"
NOW = datetime(2026, 11, 2, 19, 20, tzinfo=UTC)
IDS = {
    "fault_scope_kind": "ACCOUNT",
    "trading_account_id": ACCOUNT,
    "exchange_position_slot_claim_ids": [CLAIM],
    "symbol": "INJUSDT",
    "position_idx": 0,
}


class _Cursor:
    def __init__(self, row: dict[str, object] | None) -> None:
        self._row = row

    def fetchone(self) -> dict[str, object] | None:
        return self._row


class FakeConnection:
    def __init__(self, row: dict[str, object] | None) -> None:
        self.row = row
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        self.queries.append((sql, params))
        return _Cursor(self.row)


def clean_row() -> dict[str, object]:
    return {
        "claim_state": "RELEASED",
        "release_reason": "CONFIRMED_POSITION_CLOSE",
        "released_at": NOW - timedelta(minutes=3),
        "position_state": "CLOSED",
        "closed_at": NOW - timedelta(minutes=3),
        "close_link_status": "EXACT",
        "generation_completed_at": NOW - timedelta(seconds=3),
    }


def checked(
    row: dict[str, object] | None,
    ids: dict[str, object] | None = None,
    exposure: tuple[dict[str, object], ...] | None = (),
) -> tuple[str | None, FakeConnection]:
    from bybit_workbench.lifecycle_supervisor import LifecycleSupervisor

    connection = FakeConnection(row)
    supervisor = LifecycleSupervisor(
        connection, trading_account_id=ACCOUNT, exchange_exposure=exposure
    )
    result = supervisor._verified_closed_physical_slot_recovery(ids or IDS, NOW)
    return result, connection


def test_owner_closed_slot_requires_full_fresh_exchange_confirmation() -> None:
    result, connection = checked(clean_row())
    assert result == "EXCHANGE_AUTHORITY_CONFIRMED_POSITION_CLOSE"
    assert len(connection.queries) == 1
    sql, params = connection.queries[0]
    assert params == (CLAIM, ACCOUNT, "INJUSDT", 0)
    for guard in (
        "account_state_generations", "positions_count=0",
        "active_orders_count=0", "other.state='OPEN'",
        "RECONCILIATION_REQUIRED", "trade_commands",
    ):
        assert guard in sql


def test_ambiguous_or_stale_lineage_remains_open() -> None:
    for field, value in (
        ("claim_state", "BOUND"),
        ("release_reason", "UNKNOWN"),
        ("position_state", "OPEN"),
        ("close_link_status", "UNKNOWN"),
        ("generation_completed_at", NOW - timedelta(seconds=16)),
        ("generation_completed_at", NOW + timedelta(seconds=1)),
        ("closed_at", NOW + timedelta(minutes=1)),
        ("released_at", NOW + timedelta(minutes=1)),
    ):
        row = clean_row()
        row[field] = value
        assert checked(row)[0] is None


def test_missing_exchange_or_account_proof_fails_before_database() -> None:
    for exposure in (
        None,
        ({"trading_account_id": ACCOUNT, "symbol": "INJUSDT", "position_idx": 0},),
        ({"account_identity_binding": "VERIFIED", "trading_account_id": "foreign",
          "symbol": "INJUSDT", "position_idx": 0},),
        ({"account_identity_binding": "VERIFIED", "trading_account_id": ACCOUNT,
          "symbol": "INJUSDT", "position_idx": 0},),
    ):
        result, connection = checked(clean_row(), exposure=exposure)
        assert result is None
        assert not connection.queries


def test_foreign_account_or_ambiguous_claim_never_resolves() -> None:
    for changed in (
        {"trading_account_id": "foreign"},
        {"exchange_position_slot_claim_ids": [CLAIM, "second"]},
        {"exchange_position_slot_claim_ids": []},
        {"position_idx": "0"},
        {"fault_scope_kind": "GLOBAL"},
    ):
        result, connection = checked(clean_row(), {**IDS, **changed})
        assert result is None
        assert not connection.queries


def test_database_rejects_missing_or_multiple_ownership() -> None:
    assert checked(None)[0] is None
