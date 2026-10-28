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
    validate_real_v3_exchange_entry,
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


def _real_order_payload(*, side: str = "Buy") -> dict[str, object]:
    return {
        "source": "universal_entry",
        "strategy_id": MAIN_ID,
        "strategy_version": VERSION,
        "trading_account_id": "ta-main",
        "capital_reservation_id": "cap-p90",
        "exchange_position_slot_claim_id": "slot-p90",
        "strategy_execution_binding_id": "binding-p90",
        "entry_plan_fingerprint": "entry-p90",
        "exit_plan_fingerprint": "exit-p90",
        "stake_usdt": "10",
        "leverage": 1,
        "margin_mode": "ISOLATED",
        "price": "100",
        "entry_offset_pct": "0",
        "side": side,
        "initial_protection": {
            "stop_loss_enabled": True, "stop_loss_pct": "10",
            "take_profit_enabled": True,
            "take_profit_reference_path": "fact.p90l90.opposite_raw_l53_target",
            "take_profit_price": "110" if side == "Buy" else "90",
            "trigger_by": "LastPrice", "tpsl_mode": "Full",
        },
    }


def _signed_margin(mode: str = "ISOLATED_MARGIN") -> dict[str, object]:
    return {
        "retCode": 0, "result": {
            "marginMode": mode, "unifiedMarginStatus": 5,
        },
    }


@pytest.mark.parametrize("side", ("Buy", "Sell"))
def test_real_entry_exchange_preflight_requires_signed_isolated_and_initial_tp(side: str) -> None:
    assert validate_real_v3_exchange_entry(
        payload=_real_order_payload(side=side),
        symbol="LTCUSDT", trading_account_id="ta-main",
        signed_account_info=_signed_margin(),
    ) is True
    assert validate_real_v3_exchange_entry(
        payload={"strategy_id": "other", "strategy_version": "1"},
        symbol="BTCUSDT", trading_account_id="some-account",
        signed_account_info={},
    ) is False


@pytest.mark.parametrize("mode", ("REGULAR_MARGIN", "PORTFOLIO_MARGIN", "", "CROSS"))
def test_p90_real_never_uses_cross_or_unproven_account_margin(mode: str) -> None:
    with pytest.raises(ValueError, match="ACCOUNT_NOT_PROVEN_ISOLATED"):
        validate_real_v3_exchange_entry(
            payload=_real_order_payload(), symbol="LTCUSDT",
            trading_account_id="ta-main", signed_account_info=_signed_margin(mode),
        )


@pytest.mark.parametrize("patch,error", (
    ({"margin_mode": None}, "MARKET_ISOLATED_1X"),
    ({"margin_mode": "CROSS"}, "MARKET_ISOLATED_1X"),
    ({"leverage": 10}, "MARKET_ISOLATED_1X"),
    ({"stake_usdt": "10.01"}, "MARKET_ISOLATED_1X"),
    ({"entry_offset_pct": "0.25"}, "MARKET_ISOLATED_1X"),
    ({"strategy_version": "1.1-p90l90-strategy-second48-20261028"}, "WRONG_STRATEGY_VERSION"),
    ({"trading_account_id": "ta-sub"}, "TRADING_ACCOUNT_IDENTITY_UNPROVEN"),
))
def test_real_entry_pre_mutation_rejects_unapproved_execution(patch: dict[str, object], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        validate_real_v3_exchange_entry(
            payload={**_real_order_payload(), **patch}, symbol="LTCUSDT",
            trading_account_id="ta-main", signed_account_info=_signed_margin(),
        )


@pytest.mark.parametrize("patch,error", (
    ({"take_profit_enabled": False}, "SL_TP_CONTRACT_MISMATCH"),
    ({"take_profit_price": None}, "NUMERIC_INVALID"),
    ({"take_profit_price": "99"}, "SL_TP_CONTRACT_MISMATCH"),
    ({"take_profit_price": "NaN"}, "SL_TP_CONTRACT_MISMATCH"),
    ({"take_profit_reference_path": "fact.r1_opposite_inner_target"}, "SL_TP_CONTRACT_MISMATCH"),
    ({"stop_loss_pct": "2"}, "SL_TP_CONTRACT_MISMATCH"),
))
def test_p90_real_never_omits_or_invents_initial_tp(
    patch: dict[str, object], error: str,
) -> None:
    payload = _real_order_payload()
    payload["initial_protection"] = {
        **payload["initial_protection"], **patch,
    }
    with pytest.raises(ValueError, match=error):
        validate_real_v3_exchange_entry(
            payload=payload, symbol="LTCUSDT",
            trading_account_id="ta-main", signed_account_info=_signed_margin(),
        )


def test_real_entry_exchange_source_requires_fresh_signed_account_identity() -> None:
    for response in (
        {}, {"retCode": 1}, {"retCode": 0},
        {"retCode": 0, "result": {"marginMode": "ISOLATED_MARGIN"}},
        {"retCode": 0, "result": {
            "marginMode": "ISOLATED_MARGIN", "unifiedMarginStatus": 1,
        }},
    ):
        with pytest.raises(ValueError):
            validate_real_v3_exchange_entry(
                payload=_real_order_payload(), symbol="LTCUSDT",
                trading_account_id="ta-main", signed_account_info=response,
            )


def test_p90_isolated_margin_verified_before_first_execution_mutation() -> None:
    from pathlib import Path
    runtime = (
        Path(__file__).resolve().parents[1] / "operations/connectivity/private_runtime.py"
    ).read_text(encoding="utf-8")
    body = runtime.split("def execute_command(", 1)[1].split(
        "def _r1_signal_validity(", 1
    )[0]
    check = body.find('api_get("/v5/account/info", {}, key, secret)')
    first_mutation = body.find('"/v5/position/set-leverage"')
    assert check > 0 and first_mutation > check
    assert 'validate_real_v3_exchange_entry(' in body
    assert 'raise PreMutationSafetyBlock(str(exc))' in body
