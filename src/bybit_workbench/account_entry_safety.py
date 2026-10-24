"""Durable per-TradingAccount Entry safety latch.

This protects new Entry only. It deliberately never affects protected Exit,
reconciliation or another account's private transport. Private workers may
latch a fault, but cannot clear it. Only an independently approved control
operation following complete authenticated recovery may delete a latch.
"""

from __future__ import annotations

from typing import Any


def account_entry_safety_ready(
    connection: Any, *, trading_account_id: str, account_connector_id: str
) -> tuple[bool, tuple[str, ...]]:
    if not trading_account_id or not account_connector_id:
        return False, ("ACCOUNT_ENTRY_SAFETY_SCOPE_MISSING",)
    exists = connection.execute(
        "SELECT to_regclass('runtime.account_entry_safety_blocks')"
    ).fetchone()
    if not exists or next(iter(exists.values())) is None if isinstance(exists, dict) else (
        not exists or exists[0] is None
    ):
        return False, ("ACCOUNT_ENTRY_SAFETY_SCHEMA_MISSING",)
    matches = connection.execute(
        """SELECT 1
             FROM runtime.account_entry_safety_blocks
            WHERE trading_account_id=%s AND account_connector_id=%s""",
        (trading_account_id, account_connector_id),
    ).fetchall()
    if matches:
        return False, ("ACCOUNT_ENTRY_SAFETY_BLOCKED",)
    return True, ()


def latch_account_entry_safety(
    connection: Any, *, trading_account_id: str, account_connector_id: str,
    reason: str
) -> None:
    """Idempotent write only; preserve the first blocked reason on retries."""
    if not trading_account_id or not account_connector_id or not reason.strip():
        raise ValueError("ACCOUNT_ENTRY_SAFETY_LATCH_IDENTITY_INVALID")
    connection.execute(
        """INSERT INTO runtime.account_entry_safety_blocks(
                trading_account_id,account_connector_id,reason
           ) VALUES(%s,%s,%s)
           ON CONFLICT(trading_account_id,account_connector_id) DO NOTHING""",
        (trading_account_id, account_connector_id, reason[:300]),
    )
    connection.commit()
