"""Read-only effective-time fan-out selection from the Strategy binding registry.

No trade admission, reservation, LIVE-arm, database writes or exchange calls.
The connection must be backed by a consistent caller-owned snapshot.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from bybit_workbench.entry_account_fanout import EntryAccountFanout, select_entry_account_legs
from bybit_workbench.universal_entry.contracts import StrategyExecutionBindingRef


def read_entry_account_fanout(
    connection: Any,
    *,
    strategy_activation_id: str,
    authorized_binding_ids: frozenset[str],
    now: datetime,
) -> EntryAccountFanout:
    """Resolve ACTIVE, exact-time bindings; selection rejects ambiguous legs."""
    if now.tzinfo is None:
        raise ValueError("fan-out snapshot requires timezone-aware instant")
    activation = strategy_activation_id.strip()
    if not activation:
        return select_entry_account_legs(
            strategy_activation_id="", bindings=(),
            authorized_binding_ids=authorized_binding_ids,
        )
    instant = now.astimezone(UTC)
    rows = connection.execute(
        """SELECT strategy_execution_binding_id,strategy_activation_id,
                  execution_leg,trading_account_id
             FROM strategy_entry.strategy_execution_bindings
            WHERE strategy_activation_id=%s
              AND binding_state='ACTIVE'
              AND allocation_mode='DEDICATED_STRATEGY'
              AND effective_from<=%s
              AND (effective_to IS NULL OR effective_to>%s)
            ORDER BY execution_leg,trading_account_id,strategy_execution_binding_id""",
        (activation, instant, instant),
    ).fetchall()
    bindings = tuple(StrategyExecutionBindingRef(*row) for row in rows)
    return select_entry_account_legs(
        strategy_activation_id=activation,
        bindings=bindings,
        authorized_binding_ids=authorized_binding_ids,
    )
