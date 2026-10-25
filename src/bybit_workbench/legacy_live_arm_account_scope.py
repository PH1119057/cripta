"""Legacy release-bound LIVE-arm has no TradingAccount scope.

For the existing single-account Bybit producer/consumer, require one exact
effective account binding before relying on its Strategy/symbol arm session.
Multi-account authorization needs a separate account-scoped owner arm rail.
This is a read-only guard; it never grants execution permissions.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any


def check_legacy_live_arm_account_scope(
    connection: Any,
    *,
    strategy_activation_id: str,
    trading_account_id: str,
    now: datetime,
) -> tuple[bool, tuple[str, ...]]:
    """Validate that old Strategy-level authorization is not shared across accounts."""
    if now.tzinfo is None:
        raise ValueError("legacy arm account scope requires timezone-aware time")
    if not strategy_activation_id.strip() or not trading_account_id.strip():
        return False, ("OWNER_ARM_ACCOUNT_IDENTITY_MISSING",)
    instant = now.astimezone(UTC)
    rows = connection.execute(
        """SELECT b.trading_account_id,a.account_kind
             FROM strategy_entry.strategy_execution_bindings b
             JOIN identity.trading_accounts a
               ON a.trading_account_id=b.trading_account_id
            WHERE b.strategy_activation_id=%s
              AND b.binding_state='ACTIVE'
              AND b.effective_from<=%s
              AND (b.effective_to IS NULL OR b.effective_to>%s)""",
        (strategy_activation_id, instant, instant),
    ).fetchall()
    if len(rows) > 1:
        return False, ("MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN",)
    if len(rows) != 1:
        return False, ("OWNER_ARM_ACCOUNT_BINDING_NOT_PROVEN",)
    row = rows[0]
    actual_account = row["trading_account_id"] if isinstance(row, Mapping) else row[0]
    account_kind = row["account_kind"] if isinstance(row, Mapping) else row[1]
    if str(actual_account) != trading_account_id:
        return False, ("OWNER_ARM_ACCOUNT_BINDING_NOT_PROVEN",)
    # A singleton SUB cannot borrow Strategy/symbol-level legacy authority.
    # Only a MAIN singleton may use the historical arm compatibility rail.
    if str(account_kind) != "MAIN":
        return False, ("MULTI_ACCOUNT_OWNER_ARM_SCOPE_UNPROVEN",)
    return True, ()
