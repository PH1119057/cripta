"""Legacy Dashboard OWNER ARM supports only one proven configured TradingAccount.

The historical LIVE-arm session schema has Strategy/symbol/release but no
TradingAccount identity. This is a read-only compatibility gate, not permission
to open multiple accounts or authorize LIVE.
"""
from __future__ import annotations

from bybit_workbench.account_binding_readiness import BindingReadiness


def legacy_owner_arm_account_reasons(
    assignment: BindingReadiness,
    *,
    configured_trading_account_id: str,
) -> tuple[str, ...]:
    """Reject snapshots the existing single-account consumer cannot honor."""
    reasons: set[str] = set()
    if not assignment.ready:
        reasons.update("STRATEGY_ACCOUNT_" + reason for reason in assignment.reasons)
    expected = configured_trading_account_id.strip()
    if not expected:
        reasons.add("LEGACY_OWNER_ARM_ACCOUNT_IDENTITY_MISSING")
    if not assignment.strategy_accounts:
        reasons.add("LEGACY_OWNER_ARM_ACTIVE_STRATEGY_MISSING")
    unique_accounts = {
        account for accounts in assignment.strategy_accounts.values()
        for account in accounts
    }
    if any(len(accounts) != 1 for accounts in assignment.strategy_accounts.values()):
        reasons.add("LEGACY_OWNER_ARM_MULTI_ACCOUNT_UNSUPPORTED")
    if expected and unique_accounts != {expected}:
        reasons.add("LEGACY_OWNER_ARM_ACCOUNT_SCOPE_UNSUPPORTED")
    return tuple(sorted(reasons))
