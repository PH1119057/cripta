"""Exact account-scoped release-bound OWNER ARM reader (R04).

Read-only gate. Never creates/arms a session, turns on mainnet or changes
existing Exit. Missing R04 schema, ambiguous sessions or a mismatched
TradingAccount/Connector/ExecutionLeg are hard denials.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from bybit_workbench.strategy_execution_binding_policy import (
    real_binding_allocation_allowed,
)


def account_live_arm_ready(
    connection: Any,
    *,
    strategy_activation_id: str,
    strategy_execution_binding_id: str,
    trading_account_id: str,
    account_connector_id: str,
    strategy_id: str,
    strategy_version: str,
    strategy_config_fingerprint: str,
    symbol: str,
    release_commit: str,
    now: datetime,
) -> tuple[bool, tuple[str, ...]]:
    if (
        now.tzinfo is None
        or not strategy_activation_id
        or not strategy_execution_binding_id
        or not trading_account_id
        or not account_connector_id
        or not strategy_id
        or not strategy_version
        or not strategy_config_fingerprint
        or not symbol
        or len(release_commit) != 40
        or any(c not in "0123456789abcdef" for c in release_commit)
    ):
        return False, ("ACCOUNT_LIVE_ARM_SCOPE_INVALID",)
    # A missing migration is a block, never a downgrade to legacy authority.
    exists = connection.execute(
        "SELECT to_regclass('control.account_live_arm_sessions') AS relation_oid"
    ).fetchone()
    schema_ref = (
        None if exists is None
        else exists.get("relation_oid") if isinstance(exists, Mapping)
        else exists[0]
    )
    if schema_ref is None:
        return False, ("ACCOUNT_LIVE_ARM_SCHEMA_NOT_INSTALLED",)
    rows = connection.execute(
        """SELECT s.account_live_arm_session_id,b.allocation_mode
             FROM control.account_live_arm_sessions s
             JOIN strategy_entry.strategy_execution_bindings b
               ON b.strategy_execution_binding_id=s.strategy_execution_binding_id
             JOIN identity.trading_accounts a
               ON a.trading_account_id=s.trading_account_id
             JOIN identity.account_connectors c
               ON c.account_connector_id=s.account_connector_id
              AND c.trading_account_id=s.trading_account_id
            WHERE s.strategy_activation_id=%s
              AND s.strategy_execution_binding_id=%s
              AND s.trading_account_id=%s
              AND s.account_connector_id=%s
              AND s.strategy_id=%s
              AND s.strategy_version=%s
              AND s.strategy_config_fingerprint=%s
              AND s.symbol=%s
              AND s.release_commit=%s
              AND s.state='ACTIVE'
              AND s.owner_approved_at<=s.activated_at
              AND s.activated_at<=%s
              AND s.deactivated_at IS NULL
              AND b.strategy_activation_id=s.strategy_activation_id
              AND b.trading_account_id=s.trading_account_id
              AND b.binding_state='ACTIVE'
              AND b.effective_from<=%s
              AND (b.effective_to IS NULL OR b.effective_to>%s)
              AND a.status='ACTIVE'
              AND a.environment='MAINNET'
              AND c.status='ACTIVE'
              AND c.retired_at IS NULL
              AND c.adapter_id='BYBIT_V5_PRIVATE'
              AND c.connector_kind='CEX_PRIVATE_RUNTIME'""",
        (
            strategy_activation_id,
            strategy_execution_binding_id,
            trading_account_id,
            account_connector_id,
            strategy_id,
            strategy_version,
            strategy_config_fingerprint,
            symbol,
            release_commit,
            now.astimezone(UTC),
            now.astimezone(UTC),
            now.astimezone(UTC),
        ),
    ).fetchall()
    if len(rows) != 1:
        return False, ("EXACT_ACCOUNT_RELEASE_OWNER_ARM_NOT_ACTIVE",)
    allocation_mode = (
        str(rows[0].get("allocation_mode") or "")
        if isinstance(rows[0], Mapping)
        else str(rows[0][1])
    )
    if not real_binding_allocation_allowed(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        allocation_mode=allocation_mode,
    ):
        return False, ("ACCOUNT_BINDING_ALLOCATION_MODE_INVALID",)
    return True, ()
