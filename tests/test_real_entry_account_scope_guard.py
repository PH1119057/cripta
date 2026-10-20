from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from bybit_workbench.entry_admission import (
    EntryAdmissionRequest,
    PositionModeStateUnavailable,
    PostgresEntryAdmissionPort,
    SlotOwnershipConflict,
)
from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.strategy_position_binding import (
    exchange_position_key as original_public_position_key,
)

NOW = datetime(2026, 10, 20, 13, 0, tzinfo=UTC)


class NoDbConnection:
    def transaction(self):
        raise AssertionError("invalid REAL Entry must not start a transaction")


def request_for(account_id="ta-a"):
    return EntryAdmissionRequest(
        account_ref="BYBIT:UNIFIED",
        exchange_position_key=exchange_position_key(account_id, "BTCUSDT", 0),
        symbol="BTCUSDT",
        direction="LONG",
        strategy_id="strategy-a",
        strategy_version="v1",
        strategy_config_fingerprint="config-a",
        entry_plan_fingerprint="entry-a",
        signal_id="signal-a",
        strategy_attempt_id="attempt-a",
        requested_amount=Decimal("10"),
        amount_currency="USDT",
        capacity_snapshot_id="snapshot-a",
        capacity_observed_at=NOW,
        capacity_available=Decimal("100"),
        requested_at=NOW,
        pre_dispatch_expires_at=NOW + timedelta(minutes=2),
        account_state_generation_id="generation-a",
        strategy_activation_id="activation-a",
        strategy_execution_binding_id="binding-a",
        execution_leg="PRIMARY",
        trading_account_id=account_id,
    )


def test_missing_real_generation_is_rejected_before_database_mutation():
    port = PostgresEntryAdmissionPort(NoDbConnection())
    with pytest.raises(PositionModeStateUnavailable, match="account_state_generation_id"):
        port.admit(replace(request_for(), account_state_generation_id=None))


def test_cross_account_position_key_is_rejected_before_database_mutation():
    port = PostgresEntryAdmissionPort(NoDbConnection())
    forged = replace(
        request_for("ta-a"),
        exchange_position_key=exchange_position_key("ta-b", "BTCUSDT", 0),
    )
    with pytest.raises(SlotOwnershipConflict, match="physical slot key"):
        port.admit(forged)


def test_different_symbol_key_is_rejected_before_database_mutation():
    port = PostgresEntryAdmissionPort(NoDbConnection())
    forged = replace(
        request_for(),
        exchange_position_key=exchange_position_key("ta-a", "ETHUSDT", 0),
    )
    with pytest.raises(SlotOwnershipConflict, match="physical slot key"):
        port.admit(forged)


def test_proven_binding_identity_is_required_before_database_mutation():
    port = PostgresEntryAdmissionPort(NoDbConnection())
    forged = replace(
        request_for(),
        strategy_activation_id=None,
        strategy_execution_binding_id=None,
        trading_account_id=None,
        execution_leg=None,
    )
    with pytest.raises(RuntimeError, match="PROVEN StrategyExecutionBinding"):
        port.admit(forged)


def test_physical_key_is_distinct_across_accounts_for_identical_symbol():
    first = exchange_position_key("ta-a", "BTCUSDT", 0)
    second = exchange_position_key("ta-b", "BTCUSDT", 0)
    assert first != second
    assert original_public_position_key("ta-a", "btcusdt", 0) == first


def test_position_key_requires_explicit_account_and_valid_slot():
    with pytest.raises(ValueError, match="trading_account_id"):
        exchange_position_key("", "BTCUSDT", 0)
    with pytest.raises(ValueError, match="symbol"):
        exchange_position_key("ta-a", "", 0)
    with pytest.raises(ValueError, match="position_idx"):
        exchange_position_key("ta-a", "BTCUSDT", -1)
