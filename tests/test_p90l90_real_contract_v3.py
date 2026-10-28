"""Owner-approved P90 REAL profile contract: exact economics, no activation."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    VERSION,
    check_source_scoped_capital,
    real_v3_authoring_payload,
    real_v3_cards,
)
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import v2_authoring_payload
from bybit_workbench.universal_entry.readiness import assess_strategy_runtime_readiness

POLICY = StrategyHardStopEmbargoPolicy(
    enabled=True, hardstop_count=2, rolling_hours=48,
    new_entry_pause_hours=48, close_existing_positions=False,
    domain="STRATEGY_VERSION",
)


@pytest.mark.parametrize("ident,coins,cap", (
    (MAIN_ID, ("LTCUSDT", "INJUSDT"), "20"),
    (SUB_ID, ("DOTUSDT", "ARBUSDT", "APTUSDT"), "30"),
))
def test_real_version_is_new_immutable_authoring_only(
    ident: str, coins: tuple[str, ...], cap: str,
) -> None:
    original = v2_authoring_payload(strategy_id=ident, policy=POLICY)
    fresh = real_v3_authoring_payload(strategy_id=ident, policy=POLICY)
    assert fresh["strategy_version"] == VERSION
    assert original["strategy_version"] != VERSION
    assert fresh["symbols"] == list(coins)
    assert fresh["entry_policy"]["watch_policy"] == original["entry_policy"]["watch_policy"]
    assert fresh["exit_policy"] == original["exit_policy"]
    assert fresh["lifecycle_policy"] == original["lifecycle_policy"]
    assert fresh["entry_policy"]["execution_policy"]["order_type"] == "MARKET"
    assert fresh["entry_policy"]["execution_policy"]["max_request_age_seconds"] == 5
    money = fresh["capital_policy"]
    assert money["requested_amount"] == "10"
    assert money["leverage"] == 1
    assert money["margin_mode"] == "ISOLATED"
    assert money["max_simultaneous_committed_usdt"] == cap
    assert money["count_pending_reservations"] is True
    assert money["exchange_min_notional_if_exceeds_requested"] == "REJECT"
    assert "paper_simulation" not in money
    initial = fresh["protection_policy"]["initial_protection"]
    assert initial["stop_loss_pct"] == "10"
    assert initial["stop_loss_price_basis"] == "ACTUAL_EXECUTED_AVERAGE_ENTRY"
    assert initial["exchange_resident_required"] is True
    # Initial TP must be present from the first actual fill: not trailing-gated.
    assert initial["take_profit_enabled"] is True
    assert initial["take_profit_reference_path"] == (
        "fact.p90l90.opposite_raw_l53_target"
    )
    assert "take_profit_price" not in initial
    # Existing +0.5% activates trailing only; it cannot delay initial TP.
    assert fresh["exit_policy"]["trailing"]["activation_profit_pct"] == "0.5"
    # Old v2 is a separate immutable object, never rewritten.
    assert original["entry_policy"]["execution_policy"]["order_type"] == "NOT_APPROVED"


def test_exact_two_versions_materialize_but_do_not_self_arm() -> None:
    first, second = real_v3_cards(
        approved_at=datetime(2026, 10, 28, 18, 0, tzinfo=UTC),
        policy_by_strategy_id={MAIN_ID: POLICY, SUB_ID: POLICY},
    )
    assert (first.strategy_id, second.strategy_id) == (MAIN_ID, SUB_ID)
    assert first.strategy_version == second.strategy_version == VERSION
    assert first.strategy_config_fingerprint != second.strategy_config_fingerprint
    for card in (first, second):
        result = assess_strategy_runtime_readiness(
            card, observer_ready=False,
        )
        # Card authoring CANNOT assert unproven native execution readiness.
        assert result.execution_ready is False
        assert "P90L90_REAL_EXECUTION_ADAPTER_NOT_VERIFIED" in {
            item.code for item in result.reasons
        }
        assert "P90L90_EXCHANGE_ORDER_NOT_APPROVED" not in {
            item.code for item in result.reasons
        }


def test_real_cap_enforces_pending_plus_open_and_exact_slot() -> None:
    assert check_source_scoped_capital(
        strategy_id=MAIN_ID, symbol="LTCUSDT",
        requested_usdt=Decimal("10"), committed_usdt=Decimal("0"),
        occupied_symbols=frozenset(),
    ) == Decimal("10")
    assert check_source_scoped_capital(
        strategy_id=MAIN_ID, symbol="INJUSDT",
        requested_usdt=Decimal("10"), committed_usdt=Decimal("10"),
        occupied_symbols=frozenset({"LTCUSDT"}),
    ) == Decimal("0")
    assert check_source_scoped_capital(
        strategy_id=SUB_ID, symbol="APTUSDT",
        requested_usdt=Decimal("10"), committed_usdt=Decimal("20"),
        occupied_symbols=frozenset({"DOTUSDT", "ARBUSDT"}),
    ) == Decimal("0")
    with pytest.raises(ValueError, match="CAP_EXCEEDED"):
        check_source_scoped_capital(
            strategy_id=MAIN_ID, symbol="INJUSDT",
            requested_usdt="10", committed_usdt="20",
            occupied_symbols=frozenset({"LTCUSDT"}),
        )
    with pytest.raises(ValueError, match="ONE_POSITION"):
        check_source_scoped_capital(
            strategy_id=SUB_ID, symbol="DOTUSDT",
            requested_usdt="10", committed_usdt="10",
            occupied_symbols=frozenset({"DOTUSDT"}),
        )


@pytest.mark.parametrize("amount", ("9.99", "10.01", "-10", "NaN", "Infinity"))
def test_real_amount_fail_closed(amount: str) -> None:
    with pytest.raises(ValueError):
        check_source_scoped_capital(
            strategy_id=MAIN_ID, symbol="LTCUSDT",
            requested_usdt=amount, committed_usdt="0",
            occupied_symbols=frozenset(),
        )


def test_cross_account_occupation_is_not_copied() -> None:
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        check_source_scoped_capital(
            strategy_id=MAIN_ID, symbol="INJUSDT",
            requested_usdt="10", committed_usdt="10",
            occupied_symbols=frozenset({"DOTUSDT"}),
        )


def test_owner_approval_must_have_both_profiles() -> None:
    with pytest.raises(ValueError, match="BOTH_INDEPENDENT"):
        real_v3_cards(
            approved_at=datetime.now(UTC),
            policy_by_strategy_id={MAIN_ID: POLICY},
        )
