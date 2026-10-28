"""Account-scoped durable Exit mutation ambiguity preflight for P90 REAL.

Read-only, fail-closed. The caller MUST already own a serialized position
transaction / source checkpoint; separate SQL reads cannot themselves prove
concurrent writer exclusion. A completed command is not Exchange proof:
authenticated position/protection readback is required independently.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from bybit_workbench.exchange_position_identity import exchange_position_key
from bybit_workbench.strategy_position import StrategyPosition
from bybit_workbench.universal_entry.p90l90_real_contract_v3 import (
    ALLOWED_SYMBOLS,
    VERSION,
)


class CursorLike(Protocol):
    def fetchone(self) -> Mapping[str, object] | Sequence[object] | None: ...


class ConnectionLike(Protocol):
    def execute(self, statement: str, parameters: Sequence[object] = ()) -> CursorLike: ...


def _one(row: Mapping[str, object] | Sequence[object] | None, key: str) -> object:
    if isinstance(row, Mapping):
        return row.get(key)
    if isinstance(row, (tuple, list)) and len(row) == 1:
        return row[0]
    return None


def p90_real_exit_no_pending(
    connection: ConnectionLike,
    *,
    position: StrategyPosition,
    trading_account_id: str,
) -> bool:
    """Prove no unresolved Exit decision/dispatch/command for exact position.

    This function never grants Exchange execution permission. Missing,
    malformed, foreign-account or nonterminal state means no proof.
    """
    if (
        not trading_account_id
        or position.trading_account_id != trading_account_id
        or position.identity_status != "PROVEN"
        or position.strategy_id not in ALLOWED_SYMBOLS
        or position.strategy_version != VERSION
        or position.symbol not in ALLOWED_SYMBOLS[position.strategy_id]
        or not position.strategy_execution_binding_id
        or position.position_idx != 0
        or position.exchange_position_key != exchange_position_key(
            trading_account_id, position.symbol, 0
        )
    ):
        return False
    try:
        owned = connection.execute(
            """SELECT count(*) AS number
                 FROM runtime.position_ownership
                WHERE position_id=%s AND state='OPEN'
                  AND trading_account_id=%s
                  AND strategy_execution_binding_id=%s
                  AND identity_status='PROVEN'
                  AND strategy_id=%s AND strategy_version=%s
                  AND symbol=%s AND exchange_position_key=%s
                  AND position_idx=0""",
            (
                position.strategy_position_id, trading_account_id,
                position.strategy_execution_binding_id,
                position.strategy_id, position.strategy_version,
                position.symbol, position.exchange_position_key,
            ),
        ).fetchone()
        if _one(owned, "number") != 1:
            return False
        # Unmaterialized decisions, blocked/unknown dispatches and command
        # states without exact completion must all prevent a new mutation.
        requests = connection.execute(
            """SELECT EXISTS(
                 SELECT 1 FROM strategy_exit.exit_decisions d
                 LEFT JOIN strategy_exit.execution_requests r
                   ON r.exit_decision_id=d.exit_decision_id
                 LEFT JOIN strategy_exit.execution_dispatches v
                   ON v.execution_request_id=r.execution_request_id
                 LEFT JOIN runtime.trade_commands c
                   ON c.command_id=v.command_id
                WHERE d.strategy_position_id=%s
                  AND (
                    r.execution_request_id IS NULL
                    OR r.identity_status IS DISTINCT FROM 'PROVEN'
                    OR r.trading_account_id IS DISTINCT FROM %s
                    OR r.strategy_execution_binding_id IS DISTINCT FROM %s
                    OR v.dispatch_id IS NULL
                    OR v.state IS DISTINCT FROM 'DISPATCHED'
                    OR c.command_id IS NULL
                    OR c.command_type IS DISTINCT FROM 'strategy_exit'
                    OR c.state IS DISTINCT FROM 'completed'
                    OR c.trading_account_id IS DISTINCT FROM %s
                    OR c.strategy_execution_binding_id IS DISTINCT FROM %s
                    OR c.identity_status IS DISTINCT FROM 'PROVEN'
                  )
                ) AS pending""",
            (
                position.strategy_position_id, trading_account_id,
                position.strategy_execution_binding_id, trading_account_id,
                position.strategy_execution_binding_id,
            ),
        ).fetchone()
        if _one(requests, "pending") is not False:
            return False
        # Also block a command with no valid ExitRequest/dispatch join.
        # A wrong-account physical command must never be ignored.
        orphan = connection.execute(
            """SELECT EXISTS(
                 SELECT 1 FROM runtime.trade_commands c
                WHERE c.command_type='strategy_exit'
                  AND c.payload_json::jsonb->>'strategy_position_id'=%s
                  AND (
                    c.state IS DISTINCT FROM 'completed'
                    OR c.trading_account_id IS DISTINCT FROM %s
                    OR c.strategy_execution_binding_id IS DISTINCT FROM %s
                    OR c.identity_status IS DISTINCT FROM 'PROVEN'
                  )
                ) AS pending""",
            (
                position.strategy_position_id, trading_account_id,
                position.strategy_execution_binding_id,
            ),
        ).fetchone()
        return _one(orphan, "pending") is False
    except Exception:
        # No DB error text may be interpreted as a proven empty queue.
        return False
