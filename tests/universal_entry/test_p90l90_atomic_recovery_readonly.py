"""P90L90 restart: read-only activation proof must not request SQL row locks.

The live writable recovery path still takes FOR UPDATE. This unit test uses
an isolated statement-capturing connection: no production DB or Bybit access.
"""
from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from bybit_workbench.universal_entry.p90l90_atomic_paper_checkpoint import (
    P90L90AtomicPaperCheckpointStore,
)
from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalEntry
from bybit_workbench.universal_entry.p90l90_causal_transport_join import (
    P90L90CausalTransportJoin,
)
from bybit_workbench.universal_entry.p90l90_entry_restart_checkpoint import (
    entry_restart_snapshot,
)

SHA = "c" * 40
CLOSED = datetime(2026, 10, 28, 12, 30, tzinfo=UTC)


class Result:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[Any, ...]:
        return self._row


class Connection:
    def __init__(self, mode: str, *, source: str = SHA) -> None:
        self.mode = mode
        self.queries: list[str] = []
        join = P90L90CausalTransportJoin(
            symbol="LTCUSDT",
            closed_5m_at=CLOSED,
            closed_15m_at=CLOSED - timedelta(minutes=15),
        )
        self.row = (
            join.snapshot(),
            entry_restart_snapshot(P90L90CausalEntry()),
            source,
        )

    def transaction(self) -> Any:
        return nullcontext()

    def execute(self, sql: str, params: tuple[str, ...] | None = None) -> Result:
        self.queries.append(sql)
        if sql == "SHOW transaction_read_only":
            return Result((self.mode,))
        assert params == ("LTCUSDT",)
        return Result(self.row)


@pytest.mark.parametrize(
    ("mode", "should_lock"),
    [("on", False), ("off", True)],
)
def test_verified_restart_respects_transaction_read_only(
    mode: str, should_lock: bool,
) -> None:
    conn = Connection(mode)
    store = P90L90AtomicPaperCheckpointStore(conn, source_commit=SHA)
    result, entry = store.inspect_verified(
        symbol="LTCUSDT", recompute_geometry=lambda _symbol, _raw: None,
    )
    assert result.closed_5m_at == CLOSED
    assert entry.previous_price is None
    selects = [sql for sql in conn.queries if "FROM strategy_entry." in sql]
    assert len(selects) == 1
    assert ("FOR UPDATE" in selects[0]) is should_lock


def test_verified_restart_rejects_unproven_transaction_mode() -> None:
    conn = Connection("unknown")
    store = P90L90AtomicPaperCheckpointStore(conn, source_commit=SHA)
    with pytest.raises(RuntimeError, match="TX_MODE_UNPROVEN"):
        store.inspect_verified(
            symbol="LTCUSDT", recompute_geometry=lambda _symbol, _raw: None,
        )
    assert len(conn.queries) == 1


def test_read_only_restart_still_rejects_source_commit_mismatch() -> None:
    conn = Connection("on", source="d" * 40)
    store = P90L90AtomicPaperCheckpointStore(conn, source_commit=SHA)
    with pytest.raises(RuntimeError, match="durable state missing"):
        store.inspect_verified(
            symbol="LTCUSDT", recompute_geometry=lambda _symbol, _raw: None,
        )
