"""Read-only effective-time DB snapshot for exclusive Strategy account readiness.

Never writes, never creates REAL authorization, never assumes symbol/account
proximity is sufficient evidence. This is a pre-arm *component*, not LIVE ARM.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bybit_workbench.account_binding_readiness import (
    BindingReadiness,
    check_binding_readiness,
)
from bybit_workbench.strategy_execution_binding_policy import (
    is_owner_full_contour_subordinate_identity,
)


def read_binding_readiness(connection: Any, *, now: datetime) -> BindingReadiness:
    """Check actual active REAL StrategyActivations and active bindings.

    Caller provides the same clock instant for both queries. Missing or
    contradictory records fail closed. Account status/connector readiness,
    Entry/Exit, Exchange truth and LIVE-arm must be checked separately.
    """
    if now.tzinfo is None:
        raise ValueError("readiness evaluation requires timezone-aware now")
    instant = now.astimezone(UTC)
    strategies = connection.execute(
        """SELECT activation_id,strategy_id,strategy_version
             FROM strategy_entry.strategy_activations
            WHERE enabled=true ORDER BY activation_id"""
    ).fetchall()
    bindings = connection.execute(
        """SELECT b.strategy_execution_binding_id,b.strategy_activation_id,
                  b.trading_account_id,b.execution_leg,b.allocation_mode,
                  b.binding_state,a.strategy_id,a.strategy_version
             FROM strategy_entry.strategy_execution_bindings b
             JOIN strategy_entry.strategy_activations a
               ON a.activation_id=b.strategy_activation_id
            WHERE a.enabled=true
              AND b.binding_state='ACTIVE'
              AND b.effective_from<=%s
              AND (b.effective_to IS NULL OR b.effective_to>%s)
            ORDER BY b.strategy_activation_id,b.execution_leg,
                     b.strategy_execution_binding_id""",
        (instant, instant),
    ).fetchall()
    ordinary_strategy_ids = [
        row[0]
        for row in strategies
        if not is_owner_full_contour_subordinate_identity(str(row[1]), str(row[2]))
    ]
    field_names = (
        "strategy_execution_binding_id", "strategy_activation_id",
        "trading_account_id", "execution_leg", "allocation_mode",
        "binding_state",
    )
    ordinary_bindings = [
        dict(zip(field_names, row[:6], strict=True))
        for row in bindings
        if not is_owner_full_contour_subordinate_identity(str(row[6]), str(row[7]))
    ]
    return check_binding_readiness(
        active_strategy_ids=ordinary_strategy_ids,
        active_bindings=ordinary_bindings,
    )
