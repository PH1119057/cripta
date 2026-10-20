from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from bybit_workbench.entry_account_admission_scope import (
    AccountAdmissionEvidence,
    ScopedEntryLeg,
)
from bybit_workbench.entry_account_lineage import check_scoped_entry_request
from bybit_workbench.entry_admission import EntryAdmissionRequest
from bybit_workbench.universal_entry.contracts import StrategyExecutionBindingRef

NOW = datetime(2026, 10, 20, tzinfo=UTC)


def fixtures():
    binding = StrategyExecutionBindingRef("b-1", "a-1", "LEG_A", "ta-1")
    evidence = AccountAdmissionEvidence(
        trading_account_id="ta-1",
        account_ref="bybit-1",
        account_state_generation_id="g-1",
        capacity_snapshot_id="c-1",
        capacity_available=Decimal("100"),
        exchange_position_key="BYBIT:ta-1:BTCUSDT:0",
        connector_id="connector-1",
        private_exchange_verified=True,
        protection_capability_verified=True,
    )
    request = EntryAdmissionRequest(
        account_ref="bybit-1",
        exchange_position_key="BYBIT:ta-1:BTCUSDT:0",
        symbol="BTCUSDT",
        direction="LONG",
        strategy_id="strategy-1",
        strategy_version="v1",
        strategy_config_fingerprint="config-fingerprint",
        entry_plan_fingerprint="entry-fingerprint",
        signal_id="signal-1",
        strategy_attempt_id="attempt-1",
        requested_amount=Decimal("10"),
        amount_currency="USDT",
        capacity_snapshot_id="c-1",
        capacity_observed_at=NOW,
        capacity_available=Decimal("100"),
        requested_at=NOW,
        pre_dispatch_expires_at=NOW + timedelta(minutes=2),
        account_state_generation_id="g-1",
        strategy_activation_id="a-1",
        strategy_execution_binding_id="b-1",
        execution_leg="LEG_A",
        trading_account_id="ta-1",
    )
    return ScopedEntryLeg(binding, evidence), request


def test_account_lineage_matches_exactly():
    leg, request = fixtures()
    assert check_scoped_entry_request(leg, request).ready


def test_cross_account_request_fails_closed():
    leg, request = fixtures()
    result = check_scoped_entry_request(
        leg, replace(request, trading_account_id="ta-2")
    )
    assert "TRADING_ACCOUNT_MISMATCH" in result.reasons


def test_stale_generation_fails_closed():
    leg, request = fixtures()
    result = check_scoped_entry_request(
        leg, replace(request, account_state_generation_id="old-generation")
    )
    assert "ACCOUNT_GENERATION_MISMATCH" in result.reasons


def test_different_position_key_fails_closed():
    leg, request = fixtures()
    result = check_scoped_entry_request(
        leg, replace(request, exchange_position_key="BYBIT:other:BTCUSDT:0")
    )
    assert "EXCHANGE_POSITION_KEY_MISMATCH" in result.reasons


def test_account_snapshot_and_capacity_cannot_cross():
    leg, request = fixtures()
    changed = replace(
        request, capacity_snapshot_id="c-2", capacity_available=Decimal("90")
    )
    result = check_scoped_entry_request(leg, changed)
    assert "CAPACITY_SNAPSHOT_MISMATCH" in result.reasons
    assert "CAPACITY_VALUE_MISMATCH" in result.reasons


def test_account_capability_failure_blocks():
    leg, request = fixtures()
    changed = replace(
        leg,
        evidence=replace(leg.evidence, protection_capability_verified=False),
    )
    result = check_scoped_entry_request(changed, request)
    assert "PROTECTION_CAPABILITY_UNVERIFIED" in result.reasons
