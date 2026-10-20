from datetime import UTC, datetime
from unittest.mock import patch

import operations.connectivity.universal_entry_consumer as consumer


NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
REQUEST = {
    "strategy_id": "strategy-1",
    "strategy_version": "v1",
    "strategy_config_fingerprint": "config-1",
    "activation_id": "activation-1",
    "symbol": "BTCUSDT",
    "trading_account_id": "account-1",
}


class Rows:
    def __init__(self, accounts):
        self.accounts = accounts

    def fetchall(self):
        return [{"trading_account_id": account} for account in self.accounts]


class FakeConnection:
    def __init__(self, accounts):
        self.accounts = accounts
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return Rows(self.accounts)


def test_two_account_strategy_cannot_inherit_legacy_live_arm():
    connection = FakeConnection(("account-1", "account-2"))
    with (
        patch.object(consumer, "LOADED_RELEASE_COMMIT", "a" * 40),
        patch.object(consumer, "active_live_arm_session") as session,
    ):
        ready, reasons = consumer._live_arm_ready(connection, REQUEST, now=NOW)
    assert not ready
    assert reasons == ("MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN",)
    session.assert_not_called()


def test_single_account_still_requires_matching_account_identity():
    connection = FakeConnection(("account-2",))
    with (
        patch.object(consumer, "LOADED_RELEASE_COMMIT", "a" * 40),
        patch.object(consumer, "active_live_arm_session") as session,
    ):
        ready, reasons = consumer._live_arm_ready(connection, REQUEST, now=NOW)
    assert not ready
    assert reasons == ("OWNER_ARM_ACCOUNT_BINDING_NOT_PROVEN",)
    session.assert_not_called()


def test_single_matching_account_still_requires_exact_release_arm_session():
    connection = FakeConnection(("account-1",))
    with (
        patch.object(consumer, "LOADED_RELEASE_COMMIT", "a" * 40),
        patch.object(consumer, "active_live_arm_session", return_value="session-1") as session,
    ):
        ready, reasons = consumer._live_arm_ready(connection, REQUEST, now=NOW)
    assert ready
    assert reasons == ()
    assert session.call_count == 1
    assert "binding_state='ACTIVE'" in connection.calls[0][0]
    assert connection.calls[0][1] == ("activation-1", NOW, NOW)


def test_no_effective_account_binding_is_not_live_ready():
    connection = FakeConnection(())
    with (
        patch.object(consumer, "LOADED_RELEASE_COMMIT", "a" * 40),
        patch.object(consumer, "active_live_arm_session") as session,
    ):
        ready, reasons = consumer._live_arm_ready(connection, REQUEST, now=NOW)
    assert not ready
    assert reasons == ("OWNER_ARM_ACCOUNT_BINDING_NOT_PROVEN",)
    session.assert_not_called()
