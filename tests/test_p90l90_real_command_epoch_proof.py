"""REAL new-epoch command evidence is owned by account Execution, read only."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from operations.connectivity.p90l90_real_command_epoch_proof import (
    P90RealCommandEpochUnverified,
    read_p90_real_no_pending_runtime_commands,
)

ACCOUNTS = frozenset((
    "ta-2122c81ecff14f3b85b102db605212f7",
    "ta-853ac5fcd58185697396d33cd57459fa",
))
AT = datetime(2026, 10, 29, 13, 0, tzinfo=UTC)


class DB:
    def __init__(self, count, transaction="INTRANS"):
        self.count = count
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name=transaction)
        )
        self.sql = []

    def execute(self, sql, args):
        self.sql.append((sql, args))
        return SimpleNamespace(fetchone=lambda: (self.count,))


def test_exact_source_transaction_returns_pending_runtime_command_evidence() -> None:
    db = DB(0)
    result = read_p90_real_no_pending_runtime_commands(db, ACCOUNTS, AT)
    assert result.open_command_count == 0
    assert result.account_ids == ACCOUNTS
    assert result.checked_at == AT
    assert result.source_ref == (
        "runtime:account-scoped-command-ledger:source-transaction"
    )
    assert len(db.sql) == 1
    assert "FROM runtime.trade_commands" in db.sql[0][0]
    assert db.sql[0][1] == (sorted(ACCOUNTS),)
    assert "INSERT " not in db.sql[0][0]
    assert "UPDATE " not in db.sql[0][0]


@pytest.mark.parametrize("count,txn", ((0, "IDLE"), (1, "INTRANS")))
def test_nonterminal_command_or_missing_transaction_never_claims_flat(
    count: int, txn: str,
) -> None:
    db = DB(count, transaction=txn)
    if txn == "IDLE":
        with pytest.raises(
            P90RealCommandEpochUnverified,
            match="SOURCE_TRANSACTION_NOT_PROVEN",
        ):
            read_p90_real_no_pending_runtime_commands(db, ACCOUNTS, AT)
        assert db.sql == []
    else:
        evidence = read_p90_real_no_pending_runtime_commands(
            db, ACCOUNTS, AT
        )
        assert evidence.open_command_count == 1


def test_foreign_account_cannot_reuse_main_sub_source_snapshot() -> None:
    db = DB(0)
    with pytest.raises(P90RealCommandEpochUnverified):
        read_p90_real_no_pending_runtime_commands(
            db, frozenset(("foreign",)), AT
        )
    assert db.sql == []
