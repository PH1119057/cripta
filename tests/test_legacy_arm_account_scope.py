from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import operations.monitoring.universal_entry_shadow as observer
from bybit_workbench.legacy_live_arm_account_scope import (
    check_legacy_live_arm_account_scope,
)

NOW = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)


class Cursor:
    def __init__(self, rows=(), value=None):
        self.rows = rows
        self.value = value

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.value


class DB:
    def __init__(self, binding_rows):
        self.binding_rows = binding_rows
        self.statements = []

    def execute(self, statement, params=()):
        self.statements.append((statement, params))
        if "FROM control.execution_gates" in statement:
            return Cursor(value=(1,))
        if "FROM strategy_entry.execution_permissions" in statement:
            return Cursor(rows=[("strategy-a", "1", "cfg-a")])
        if "FROM strategy_entry.strategy_execution_bindings" in statement:
            return Cursor(rows=self.binding_rows)
        raise AssertionError(f"unexpected query: {statement}")


def check(db, account_id="account-a"):
    return check_legacy_live_arm_account_scope(
        db, strategy_activation_id="activation-a", trading_account_id=account_id,
        now=NOW,
    )


@pytest.mark.parametrize(
    "rows",
    [
        [{"trading_account_id": "account-a", "account_kind": "MAIN"}, {"trading_account_id": "account-b", "account_kind": "SUB"}],
        [("account-a", "MAIN"), ("account-b", "SUB")],
    ],
)
def test_two_accounts_never_inherit_legacy_strategy_arm(rows):
    db = DB(rows)
    assert check(db) == (False, ("MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN",))


def test_one_account_can_proceed_to_separate_release_session_check():
    db = DB([("account-a", "MAIN")])
    assert check(db) == (True, ())
    sql, params = db.statements[-1]
    assert "effective_from<=%s" in sql
    assert params == ("activation-a", NOW, NOW)


def test_wrong_account_and_missing_binding_fail_closed():
    assert check(DB([{"trading_account_id": "account-b", "account_kind": "MAIN"}]))[0] is False
    assert check(DB([])) == (False, ("OWNER_ARM_ACCOUNT_BINDING_NOT_PROVEN",))


def test_missing_identity_or_timezone_avoids_unproven_authorization():
    db = DB([("account-a", "MAIN")])
    assert check(db, account_id="") == (False, ("OWNER_ARM_ACCOUNT_IDENTITY_MISSING",))
    assert db.statements == []
    with pytest.raises(ValueError):
        check_legacy_live_arm_account_scope(
            db, strategy_activation_id="activation-a",
            trading_account_id="account-a", now=datetime(2026, 10, 20),
        )


def test_observer_rejects_multiaccount_activation_before_session_lookup():
    db = DB([("account-a", "MAIN"), ("account-b", "SUB")])
    bundle = SimpleNamespace(
        card=SimpleNamespace(
            strategy_id="strategy-a", strategy_version="1",
            strategy_config_fingerprint="cfg-a", symbols=("BTCUSDT",),
        ),
        activation=SimpleNamespace(activation_id="activation-a"),
    )
    with (
        patch.object(observer, "LOADED_COMMIT", "a" * 40),
        patch.object(observer, "TRADING_ACCOUNT_ID", "account-a"),
        patch.object(observer, "active_live_arm_session") as session,
    ):
        enabled = observer._real_execution_activation_ids(db, (bundle,))
    assert enabled == frozenset()
    session.assert_not_called()


def test_observer_retains_single_account_exact_session_requirement():
    db = DB([("account-a", "MAIN")])
    bundle = SimpleNamespace(
        card=SimpleNamespace(
            strategy_id="strategy-a", strategy_version="1",
            strategy_config_fingerprint="cfg-a", symbols=("BTCUSDT",),
        ),
        activation=SimpleNamespace(activation_id="activation-a"),
    )
    with (
        patch.object(observer, "LOADED_COMMIT", "a" * 40),
        patch.object(observer, "TRADING_ACCOUNT_ID", "account-a"),
        patch.object(observer, "active_live_arm_session", return_value="session-a") as session,
    ):
        enabled = observer._real_execution_activation_ids(db, (bundle,))
    assert enabled == frozenset({"activation-a"})
    session.assert_called_once()
