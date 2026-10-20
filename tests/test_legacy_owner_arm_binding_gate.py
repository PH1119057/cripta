from pathlib import Path

from bybit_workbench.account_binding_readiness import BindingReadiness
from bybit_workbench.legacy_owner_arm_binding_gate import (
    legacy_owner_arm_account_reasons,
)


def snapshot(strategy_accounts, *, ready=True, reasons=()):
    return BindingReadiness(
        ready=ready, reasons=tuple(reasons), strategy_accounts=strategy_accounts
    )


def check(assignment, account="account-1"):
    return legacy_owner_arm_account_reasons(
        assignment, configured_trading_account_id=account
    )


def test_one_active_strategy_one_configured_account_is_legacy_compatible():
    assert check(snapshot({"activation-a": ("account-1",)})) == ()


def test_multiple_activations_sharing_the_same_account_are_not_sufficient():
    result = check(snapshot(
        {"activation-a": ("account-1",), "activation-b": ("account-1",)},
        ready=False,
        reasons=("TRADING_ACCOUNT_SHARED_BY_STRATEGIES",),
    ))
    assert "STRATEGY_ACCOUNT_TRADING_ACCOUNT_SHARED_BY_STRATEGIES" in result


def test_two_accounts_on_one_strategy_fail_closed_even_when_binding_snapshot_ready():
    result = check(snapshot({"activation-a": ("account-1", "account-2")}))
    assert "LEGACY_OWNER_ARM_MULTI_ACCOUNT_UNSUPPORTED" in result
    assert "LEGACY_OWNER_ARM_ACCOUNT_SCOPE_UNSUPPORTED" in result


def test_separate_strategies_on_different_accounts_are_not_armable_as_one_scope():
    result = check(snapshot({
        "activation-a": ("account-1",),
        "activation-b": ("account-2",),
    }))
    assert "LEGACY_OWNER_ARM_ACCOUNT_SCOPE_UNSUPPORTED" in result


def test_other_single_account_mismatch_fails_closed():
    assert check(snapshot({"activation-a": ("account-2",)})) == (
        "LEGACY_OWNER_ARM_ACCOUNT_SCOPE_UNSUPPORTED",
    )


def test_missing_account_and_empty_activations_fail_closed():
    result = check(snapshot({}), account="")
    assert "LEGACY_OWNER_ARM_ACCOUNT_IDENTITY_MISSING" in result
    assert "LEGACY_OWNER_ARM_ACTIVE_STRATEGY_MISSING" in result


def test_dashboard_checks_readiness_and_owner_mutation_before_session_insert():
    source = Path("operations/dashboard/app.py").read_text(encoding="utf-8")
    start = source.index("def live_rearm_readiness(")
    end = source.index("\ndef live_trading_state(", start)
    assert "legacy_owner_arm_account_reasons(" in source[start:end]
    owner = source.index("legacy OWNER ARM account scope blocked:")
    session = source.index("INSERT INTO control.live_arm_sessions(")
    assert owner < session
    assert "check_legacy_live_arm_account_scope(" in source[owner - 520:owner]
