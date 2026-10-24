from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import operations.connectivity.universal_entry_consumer as consumer
from bybit_workbench.account_live_arm import account_live_arm_ready

NOW = datetime(2026, 10, 24, 18, 0, tzinfo=UTC)
RELEASE = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


class Cursor:
    def __init__(self, values: list[tuple[object, ...]]) -> None:
        self.values = values

    def fetchone(self) -> tuple[object, ...] | None:
        return self.values[0] if self.values else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.values


class DB:
    def __init__(self, exists: bool = True, active: bool = True) -> None:
        self.exists = exists
        self.active = active
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Cursor:
        self.queries.append((sql, params))
        if "to_regclass" in sql:
            return Cursor([("control.account_live_arm_sessions",)] if self.exists else [(None,)])
        return Cursor([("owner-armed-session",)] if self.active else [])


def check(db: DB, *, connector: str = "conn-a", binding: str = "binding-a") -> tuple[bool, tuple[str, ...]]:
    return account_live_arm_ready(
        db,
        strategy_activation_id="act-a",
        strategy_execution_binding_id=binding,
        trading_account_id="account-a",
        account_connector_id=connector,
        strategy_id="strategy-a",
        strategy_version="v1",
        strategy_config_fingerprint="strategy-fp-a",
        symbol="BTCUSDT",
        release_commit=RELEASE,
        now=NOW,
    )


def test_migration_adds_only_empty_selectable_owner_session_registry() -> None:
    source = (ROOT / "operations/sql/20261024_account_scoped_live_arm.sql").read_text()
    upper = source.upper()
    assert "CREATE TABLE IF NOT EXISTS control.account_live_arm_sessions" in source
    assert "GRANT SELECT ON control.account_live_arm_sessions TO cripta" in source
    assert "OWNER_APPROVAL_SOURCE" in upper
    assert "STRATEGY_EXECUTION_BINDING_ID" in upper
    assert "ACCOUNT_CONNECTOR_ID" in upper
    assert "TRADING_ACCOUNT_ID" in upper
    assert "INSERT INTO CONTROL.ACCOUNT_LIVE_ARM_SESSIONS" not in upper
    assert "CONTROL.EXECUTION_GATES" not in upper
    assert "CONTROL.LIVE_ARM_SESSIONS" not in upper.replace(
        "CREATE TABLE IF NOT EXISTS CONTROL.ACCOUNT_LIVE_ARM_SESSIONS", ""
    )


def test_account_arm_gate_rejects_missing_schema_without_legacy_fallback() -> None:
    db = DB(exists=False)
    assert check(db) == (False, ("ACCOUNT_LIVE_ARM_SCHEMA_NOT_INSTALLED",))
    assert len(db.queries) == 1


def test_account_arm_gate_selects_exact_account_connector_binding_release_symbol() -> None:
    db = DB()
    assert check(db) == (True, ())
    sql, params = db.queries[1]
    assert "control.account_live_arm_sessions s" in sql
    assert "s.state='ACTIVE'" in sql
    assert "b.trading_account_id=s.trading_account_id" in sql
    assert "c.trading_account_id=s.trading_account_id" in sql
    assert "c.retired_at IS NULL" in sql
    assert params[:9] == (
        "act-a", "binding-a", "account-a", "conn-a",
        "strategy-a", "v1", "strategy-fp-a", "BTCUSDT", RELEASE,
    )


def test_missing_exact_active_session_does_not_authorize() -> None:
    assert check(DB(active=False)) == (
        False, ("EXACT_ACCOUNT_RELEASE_OWNER_ARM_NOT_ACTIVE",)
    )


def test_invalid_release_and_missing_credential_connector_fail_without_query() -> None:
    db = DB()
    assert check(db, connector="") == (False, ("ACCOUNT_LIVE_ARM_SCOPE_INVALID",))
    assert not db.queries


def test_multi_account_consumer_never_uses_old_strategy_level_session() -> None:
    row = {
        "strategy_id": "strategy-a",
        "strategy_version": "v1",
        "strategy_config_fingerprint": "strategy-fp-a",
        "activation_id": "act-a",
        "strategy_execution_binding_id": "binding-a",
        "trading_account_id": "account-a",
        "symbol": "BTCUSDT",
    }
    with (
        patch.object(consumer, "LOADED_RELEASE_COMMIT", RELEASE),
        patch.object(consumer, "ACCOUNT_CONNECTOR_ID", "conn-a"),
        patch.object(
            consumer,
            "check_legacy_live_arm_account_scope",
            return_value=(False, ("MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN",)),
        ),
        patch.object(
            consumer, "account_live_arm_ready", return_value=(True, ())
        ) as scoped,
        patch.object(consumer, "active_live_arm_session") as legacy_session,
    ):
        ready, reasons = consumer._live_arm_ready(DB(), row, now=NOW)
    assert ready and reasons == ()
    assert scoped.call_args.kwargs["trading_account_id"] == "account-a"
    assert scoped.call_args.kwargs["account_connector_id"] == "conn-a"
    assert scoped.call_args.kwargs["release_commit"] == RELEASE
    legacy_session.assert_not_called()


def test_multi_account_denied_when_account_session_missing() -> None:
    row = {
        "strategy_id": "strategy-a", "strategy_version": "v1",
        "strategy_config_fingerprint": "strategy-fp-a",
        "activation_id": "act-a", "strategy_execution_binding_id": "binding-a",
        "trading_account_id": "account-a", "symbol": "BTCUSDT",
    }
    with (
        patch.object(consumer, "LOADED_RELEASE_COMMIT", RELEASE),
        patch.object(consumer, "ACCOUNT_CONNECTOR_ID", "conn-a"),
        patch.object(
            consumer, "check_legacy_live_arm_account_scope",
            return_value=(False, ("MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN",)),
        ),
        patch.object(
            consumer, "account_live_arm_ready",
            return_value=(False, ("EXACT_ACCOUNT_RELEASE_OWNER_ARM_NOT_ACTIVE",)),
        ),
    ):
        assert consumer._live_arm_ready(DB(), row, now=NOW) == (
            False, ("EXACT_ACCOUNT_RELEASE_OWNER_ARM_NOT_ACTIVE",),
        )
