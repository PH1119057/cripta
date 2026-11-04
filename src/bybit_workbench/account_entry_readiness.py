"""One current account-state predicate for prearm and real candidate admission.

Historical faults attached to CLOSED positions are repair history, not current
Entry blockers. Unknown/nonterminal entities remain fail-closed; independent
account/slot/reservation and Exchange mutation owners retain all checks.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .account_state_generation import (
    AccountStateGenerationUnavailable,
    current_complete_account_state_generation,
)
from .universal_entry.contracts import DataQuality, TechnicalReadiness, TradingCapacitySnapshot


def real_entry_account_state(
    connection: Any,
    *,
    symbol: str,
    observed_at: datetime,
    trading_account_id: str,
) -> tuple[TechnicalReadiness, TradingCapacitySnapshot | None]:
    exact_account = trading_account_id
    if not exact_account:
        return TechnicalReadiness(False, observed_at, "TradingAccount identity missing"), None
    try:
        generation = current_complete_account_state_generation(
            connection,
            account_ref="BYBIT:UNIFIED",
            trading_account_id=exact_account,
            acquire_account_lock=True,
        )
    except AccountStateGenerationUnavailable as exc:
        return TechnicalReadiness(False, observed_at, str(exc)), None

    state_ref = generation.position_mode_refs.get(symbol)
    if not state_ref:
        return (
            TechnicalReadiness(
                False,
                observed_at,
                f"current account state generation has no position-mode proof for {symbol}",
            ),
            None,
        )

    critical_fault = connection.execute(
        """SELECT f.fault_code
             FROM runtime.lifecycle_faults f
             LEFT JOIN runtime.position_ownership p
               ON p.position_id=NULLIF(f.exact_ids->>'strategy_position_id','')
            WHERE f.state='OPEN' AND f.severity='CRITICAL'
              AND (NULLIF(f.exact_ids->>'trading_account_id','') IS NULL
                   OR f.exact_ids->>'trading_account_id'=%s)
              AND (p.position_id IS NULL OR p.state<>'CLOSED')
            ORDER BY f.detected_at,f.fault_id LIMIT 1""",
        (exact_account,),
    ).fetchone()
    if critical_fault is not None:
        return (
            TechnicalReadiness(
                False,
                observed_at,
                f"open critical lifecycle fault: {critical_fault[0]}",
            ),
            None,
        )

    capacity = TradingCapacitySnapshot(
        capacity_snapshot_id=generation.generation_id,
        observed_at=generation.completed_at,
        available_for_new_trading=generation.available_balance,
        quality=DataQuality.HIGH,
        source_ref=f"runtime.account_state_generations:{generation.generation_id}",
    )
    return (
        TechnicalReadiness(
            True,
            observed_at,
            f"complete account state generation {generation.generation_id}",
        ),
        capacity,
    )

