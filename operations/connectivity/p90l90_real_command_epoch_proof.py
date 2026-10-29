"""Credential-independent account Execution command-ledger read for P90.

Read only; used within the EXACT ongoing serializable source transaction.
Runtime command ownership stays in Execution, never universal Entry.
Does not check Exchange private inventory, authorize an epoch or mutate LIVE.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from bybit_workbench.universal_entry.p90l90_real_source_epoch_start import (
    P90RealNoPendingRuntimeCommandEvidence,
)

_ACCOUNTS = frozenset((
    "ta-2122c81ecff14f3b85b102db605212f7",
    "ta-853ac5fcd58185697396d33cd57459fa",
))


class P90RealCommandEpochUnverified(RuntimeError):
    """Source transaction's exact MAIN/SUB command state not proven."""


def read_p90_real_no_pending_runtime_commands(
    connection: Any, accounts: frozenset[str], at: datetime,
) -> P90RealNoPendingRuntimeCommandEvidence:
    """Return durable account-scoped nonterminal-command inventory evidence."""
    if (
        accounts != _ACCOUNTS
        or at.tzinfo is None or at.utcoffset() is None
        or getattr(
            getattr(getattr(connection, "info", None), "transaction_status", None),
            "name", None,
        ) != "INTRANS"
    ):
        raise P90RealCommandEpochUnverified(
            "P90_REAL_ACCOUNT_COMMAND_SOURCE_TRANSACTION_NOT_PROVEN"
        )
    row = connection.execute(
        """SELECT count(*)
             FROM runtime.trade_commands
            WHERE trading_account_id=ANY(%s)
              AND state NOT IN ('completed','failed','cancelled','rejected')""",
        (sorted(accounts),),
    ).fetchone()
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 1
        or type(row[0]) is not int
        or row[0] < 0
    ):
        raise P90RealCommandEpochUnverified(
            "P90_REAL_ACCOUNT_COMMAND_LEDGER_INCOMPLETE"
        )
    return P90RealNoPendingRuntimeCommandEvidence(
        account_ids=accounts,
        checked_at=at,
        open_command_count=row[0],
        source_ref="runtime:account-scoped-command-ledger:source-transaction",
    )
