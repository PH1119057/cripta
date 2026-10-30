"""Issue #235: no account-local scan may resolve another account's fault.

Pure tests intentionally do not contact Bybit, production services or PostgreSQL.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.lifecycle_supervisor import (
    FaultCondition,
    FaultSeverity,
    LifecycleSupervisor,
)

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
ACCOUNT_A = "ta-a"
ACCOUNT_B = "ta-b"


class _Cursor:
    def __init__(self, rows: list[dict[str, object]], *, rowcount: int = 0):
        self._rows = rows
        self.rowcount = rowcount

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows

    def fetchone(self) -> dict[str, object] | None:
        return self._rows[0] if self._rows else None


class _FaultConnection:
    def __init__(self, rows: list[dict[str, object]]):
        self.rows = {str(row["fault_id"]): dict(row) for row in rows}

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> _Cursor:
        if "SELECT fault_id,fault_code,exact_ids" in statement:
            return _Cursor(
                [dict(row) for row in self.rows.values() if row["state"] == "OPEN"]
            )
        if "UPDATE runtime.lifecycle_faults" in statement and "state='RESOLVED'" in statement:
            _when, fault_id = parameters
            row = self.rows[str(fault_id)]
            if row["state"] != "OPEN":
                return _Cursor([], rowcount=0)
            row["state"] = "RESOLVED"
            return _Cursor([], rowcount=1)
        raise AssertionError("unexpected SQL in isolated fault-resolution test")


def _row(
    name: str,
    code: str,
    *,
    account: str | None = None,
    kind: str | None = None,
) -> dict[str, object]:
    exact_ids: dict[str, object] = {"strategy_position_id": name}
    if kind is not None:
        exact_ids["fault_scope_kind"] = kind
    if account is not None:
        exact_ids["trading_account_id"] = account
    return {
        "fault_id": name,
        "fault_code": code,
        "exact_ids": exact_ids,
        "state": "OPEN",
    }


def _condition() -> FaultCondition:
    return FaultCondition(
        code="POSITION_WITHOUT_EXIT_OWNER",
        severity=FaultSeverity.CRITICAL,
        scope_key="same-symbol-and-position",
        strategy_position_id="same-symbol-and-position",
        exact_ids={"strategy_position_id": "same-symbol-and-position"},
        payload={"reason": "exit owner unproven"},
    )


def test_two_accounts_with_identical_local_position_identity_never_share_fault_id() -> None:
    a = LifecycleSupervisor(_FaultConnection([]), trading_account_id=ACCOUNT_A)
    b = LifecycleSupervisor(_FaultConnection([]), trading_account_id=ACCOUNT_B)
    assert a._fault_id(_condition()) != b._fault_id(_condition())
    assert a._fault_id(_condition()) == a._fault_id(_condition())


def test_new_account_fault_carries_proven_owner_and_mismatch_fails_closed() -> None:
    b = LifecycleSupervisor(_FaultConnection([]), trading_account_id=ACCOUNT_B)
    scoped = b._scoped_fault(_condition())
    assert scoped.exact_ids["fault_scope_kind"] == "ACCOUNT"
    assert scoped.exact_ids["trading_account_id"] == ACCOUNT_B
    wrong = FaultCondition(
        code=_condition().code,
        severity=FaultSeverity.CRITICAL,
        scope_key="same-symbol-and-position",
        strategy_position_id=None,
        exact_ids={"trading_account_id": ACCOUNT_A},
        payload={},
    )
    with pytest.raises(RuntimeError, match="another TradingAccount"):
        b._scoped_fault(wrong)


def test_sub_scan_never_resolves_main_global_or_legacy_unknown_faults() -> None:
    db = _FaultConnection(
        [
            _row("a-open", "POSITION_WITHOUT_EXIT_OWNER", account=ACCOUNT_A, kind="ACCOUNT"),
            _row("b-open", "POSITION_WITHOUT_EXIT_OWNER", account=ACCOUNT_B, kind="ACCOUNT"),
            _row("no-proof", "POSITION_WITHOUT_EXIT_OWNER"),
            _row("global", "PLAN_PAIR_INCOMPLETE", kind="GLOBAL"),
        ]
    )
    sub = LifecycleSupervisor(db, trading_account_id=ACCOUNT_B, owns_global_faults=False)
    assert sub._resolve_cleared_faults((), NOW) == 1
    assert db.rows["b-open"]["state"] == "RESOLVED"
    for key in ("a-open", "no-proof", "global"):
        assert db.rows[key]["state"] == "OPEN"


def test_single_global_owner_can_resolve_global_but_not_foreign_account_fault() -> None:
    db = _FaultConnection(
        [
            _row("global", "ENTRY_PLAN_NOT_CONSUMED", kind="GLOBAL"),
            _row("global-legacy", "PLAN_PAIR_INCOMPLETE"),
            _row("b-open", "POSITION_WITHOUT_EXIT_OWNER", account=ACCOUNT_B, kind="ACCOUNT"),
        ]
    )
    main = LifecycleSupervisor(db, trading_account_id=ACCOUNT_A, owns_global_faults=True)
    assert main._resolve_cleared_faults((), NOW) == 2
    assert db.rows["global"]["state"] == "RESOLVED"
    assert db.rows["global-legacy"]["state"] == "RESOLVED"
    assert db.rows["b-open"]["state"] == "OPEN"


def test_account_resolution_needs_explicit_matching_scope_not_only_account_id() -> None:
    db = _FaultConnection(
        [
            _row("legacy-account", "POSITION_WITHOUT_EXIT_OWNER", account=ACCOUNT_A),
            _row("wrong-kind", "POSITION_WITHOUT_EXIT_OWNER", account=ACCOUNT_A, kind="GLOBAL"),
            _row("own-proven", "POSITION_WITHOUT_EXIT_OWNER", account=ACCOUNT_A, kind="ACCOUNT"),
        ]
    )
    main = LifecycleSupervisor(db, trading_account_id=ACCOUNT_A, owns_global_faults=True)
    assert main._resolve_cleared_faults((), NOW) == 1
    assert db.rows["legacy-account"]["state"] == "OPEN"
    assert db.rows["wrong-kind"]["state"] == "OPEN"
    assert db.rows["own-proven"]["state"] == "RESOLVED"


def test_legacy_unscoped_is_not_authorized_to_clear_explicit_account_fault() -> None:
    db = _FaultConnection(
        [_row("b-open", "POSITION_WITHOUT_EXIT_OWNER", account=ACCOUNT_B, kind="ACCOUNT")]
    )
    legacy = LifecycleSupervisor(db)
    assert legacy._resolve_cleared_faults((), NOW) == 0
    assert db.rows["b-open"]["state"] == "OPEN"


class _ProjectionConnection:
    """Empty state with a real SQL-call ledger for account-vs-global assertions."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, parameters: tuple[object, ...] = ()) -> _Cursor:
        self.statements.append(statement)
        return _Cursor([])


def test_account_local_projection_does_not_replay_global_sources() -> None:
    db = _ProjectionConnection()
    local = LifecycleSupervisor(db, trading_account_id=ACCOUNT_B, owns_global_faults=False)
    assert local._project_events(NOW) == 0
    assert any("FROM runtime.exchange_position_slot_claims" in stmt for stmt in db.statements)
    for source in (
        "FROM strategy_entry.strategy_activations",
        "FROM runtime.plan_consumptions",
        "FROM strategy_entry.strategy_signals",
        "FROM strategy_entry.strategy_attempts",
        "FROM strategy_entry.entry_decisions",
        "FROM strategy_entry.execution_requests",
        "FROM strategy_entry.execution_request_state_events",
        "FROM strategy_exit.execution_dispatches",
    ):
        assert all(source not in stmt for stmt in db.statements), source


def test_global_coordinator_retains_all_required_plan_projections() -> None:
    db = _ProjectionConnection()
    global_owner = LifecycleSupervisor(
        db, trading_account_id=ACCOUNT_A, owns_global_faults=True
    )
    assert global_owner._project_events(NOW) == 0
    assert any(
        "FROM strategy_entry.strategy_activations" in stmt
        for stmt in db.statements
    )
    assert any("FROM runtime.plan_consumptions" in stmt for stmt in db.statements)
    assert any(
        "FROM strategy_exit.execution_dispatches" in stmt for stmt in db.statements
    )


def test_account_local_fault_detector_never_claims_global_strategy_plan_owner() -> None:
    db = _ProjectionConnection()
    local = LifecycleSupervisor(db, trading_account_id=ACCOUNT_B, owns_global_faults=False)
    assert local._detect_faults(NOW) == ()
    assert not any(
        "FROM strategy_entry.strategy_activations a" in stmt
        for stmt in db.statements
    )
