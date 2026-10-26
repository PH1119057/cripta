from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bybit_workbench.strategy_financial_account_service import (
    inspect_financial_account_choice,
    record_financial_account_choice,
)

NOW = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)
ROW = (
    "venue-bybit", "MAINNET", "ACTIVE", "MAIN", "UNIFIED", "111222",
    "ta-main", "CEX_PRIVATE_RUNTIME", "BYBIT_V5_PRIVATE", "ACTIVE",
    "credential:main", None,
)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class Connection:
    def __init__(self, account_rows=None, *, serializable=True, activation=True,
                 overlap=False, connector_rows=None):
        self.account_rows = [ROW] if account_rows is None else account_rows
        self.connector_rows = [("ac-main",)] if connector_rows is None else connector_rows
        self.serializable = serializable
        self.activation = activation
        self.overlap = overlap
        self.insertions = []

    def execute(self, sql, params=None):
        if sql == "SHOW transaction_isolation":
            return Cursor([("serializable" if self.serializable else "read committed",)])
        if "FROM identity.trading_accounts a" in sql:
            assert params == ("ta-main", "ac-main")
            return Cursor(self.account_rows)
        if "FROM identity.account_connectors" in sql:
            assert params == ("ta-main",)
            return Cursor(self.connector_rows)
        if "FROM strategy_entry.strategy_activations" in sql:
            return Cursor([("activation-1",)] if self.activation else [])
        if "FROM strategy_entry.strategy_execution_bindings" in sql:
            return Cursor([("someone-else",)] if self.overlap else [])
        if "INSERT INTO strategy_entry.strategy_execution_bindings" in sql:
            self.insertions.append((sql, params))
            return Cursor([])
        raise AssertionError("unexpected SQL")


def bind(connection, *, operator="owner"):
    return record_financial_account_choice(
        connection, strategy_execution_binding_id="binding-1",
        strategy_activation_id="activation-1", execution_leg="PRIMARY",
        trading_account_id="ta-main", account_connector_id="ac-main",
        effective_from=NOW, operator=operator, reason="operator chose account",
    )


def test_selectable_account_requires_registered_uid_and_active_connector() -> None:
    c = Connection()
    result = inspect_financial_account_choice(
        c, trading_account_id="ta-main", account_connector_id="ac-main",
    )
    assert result.ready
    assert result.reasons == ()
    no_uid = Connection(account_rows=[(*ROW[:5], None, *ROW[6:])])
    result = inspect_financial_account_choice(
        no_uid, trading_account_id="ta-main", account_connector_id="ac-main",
    )
    assert not result.ready
    assert "FINANCIAL_ACCOUNT_PROVIDER_UID_NOT_REGISTERED" in result.reasons
    assert not inspect_financial_account_choice(
        Connection(account_rows=[]),
        trading_account_id="ta-main", account_connector_id="ac-main",
    ).ready


def test_selection_is_binding_not_strategy_policy_or_live_arm() -> None:
    connection = Connection()
    bind(connection)
    assert len(connection.insertions) == 1
    sql, values = connection.insertions[0]
    assert "strategy_entry.strategy_execution_bindings" in sql
    assert "DEDICATED_STRATEGY" in sql
    assert "OWNER_FINANCIAL_ACCOUNT_SELECTION" in sql
    assert "strategy_cards" not in sql.lower()
    assert "execution_gates" not in sql
    assert "strategy_activations SET enabled" not in sql
    assert values[0:4] == (
        "binding-1", "activation-1", "PRIMARY", "ta-main",
    )


def test_binding_blocks_unapproved_or_nonserializable_actor() -> None:
    for connection, operator, error in (
        (Connection(), "", "INPUT_MISSING"),
        (Connection(serializable=False), "owner", "SERIALIZABLE_REQUIRED"),
        (Connection(overlap=True), "owner", "ALREADY_BOUND"),
        (Connection(activation=False), "owner", "NOT_REGISTERED"),
        (Connection(account_rows=[(*ROW[:5], None, *ROW[6:])]),
         "owner", "CHOICE_BLOCKED"),
    ):
        with pytest.raises(RuntimeError, match=error):
            bind(connection, operator=operator)
        assert connection.insertions == []


def test_duplicate_or_retired_connector_is_not_bindable() -> None:
    duplicate = Connection(account_rows=[ROW, ROW])
    assert not inspect_financial_account_choice(
        duplicate, trading_account_id="ta-main", account_connector_id="ac-main"
    ).ready
    retired_row = (*ROW[:11], NOW)
    retired = Connection(account_rows=[retired_row])
    choice = inspect_financial_account_choice(
        retired, trading_account_id="ta-main", account_connector_id="ac-main"
    )
    assert not choice.ready
    assert "FINANCIAL_ACCOUNT_PRIVATE_CONNECTOR_NOT_READY" in choice.reasons


def test_multi_connector_account_cannot_be_persisted_as_unqualified_binding() -> None:
    multi = Connection(connector_rows=[("ac-main",), ("ac-other",)])
    choice = inspect_financial_account_choice(
        multi, trading_account_id="ta-main", account_connector_id="ac-main"
    )
    assert not choice.ready
    assert "FINANCIAL_ACCOUNT_CONNECTOR_AMBIGUOUS" in choice.reasons
    with pytest.raises(RuntimeError, match="CHOICE_BLOCKED"):
        bind(multi)
    assert multi.insertions == []


def test_nonprimary_execution_leg_rejected_until_producer_fanout_exists() -> None:
    c = Connection()
    with pytest.raises(RuntimeError, match="EXECUTION_LEG_UNSUPPORTED"):
        record_financial_account_choice(
            c, strategy_execution_binding_id="binding-b", 
            strategy_activation_id="activation-1", execution_leg="LEG_B",
            trading_account_id="ta-main", account_connector_id="ac-main",
            effective_from=NOW, operator="owner", reason="candidate",
        )
    assert c.insertions == []
