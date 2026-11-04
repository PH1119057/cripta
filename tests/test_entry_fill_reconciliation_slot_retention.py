"""Regression for the confirmed real Entry / stale reconciliation race.

The in-flight snapshot may be flat even after a new fill. An unlinked close
must NOT release the physical slot or capital reservation.
No production DB, network, Bybit or Exchange mutation in this suite.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# Match the production script directory for its sibling imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operations/connectivity"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research/server/connectivity"))

from operations.connectivity import private_runtime  # noqa: E402

ACCOUNT = "ta-2122c81ecff14f3b85b102db605212f7"
POSITION = "POS-test-entry-ltc"
SLOT = "slot-test-entry-ltc"


class Rows:
    def __init__(self, items: list[tuple[object, ...]]) -> None:
        self.items = items
        self.rowcount = len(items)

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.items

    def fetchone(self) -> tuple[object, ...] | None:
        return self.items[0] if self.items else None


class DB:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, sql: str, _params: object = ()) -> Rows:
        self.statements.append(sql)
        if "FROM runtime.position_ownership" in sql and "ORDER BY fill_at" in sql:
            return Rows([(
                POSITION, "trade-entry-ltc", "LTCUSDT", "Sell",
                "104.5", "0.1", 1793790910007, 0,
                "ue-entry-ltc", "request-entry-ltc",
                f"BYBIT:{ACCOUNT}:LINEAR:USDT:LTCUSDT:0",
                "strategy-ltc", "2.0",
            )])
        if "SELECT extract(epoch from min(fill_at))" in sql:
            return Rows([(None,)])
        if "FROM runtime.executions" in sql:
            return Rows([])
        if "UPDATE runtime.position_ownership" in sql:
            return Rows([(1,)])
        raise AssertionError("unexpected query in synthetic test: " + sql[:120])


def test_stale_empty_snapshot_never_releases_recent_confirmed_entry_slot() -> None:
    connection = DB()
    unresolved = SimpleNamespace(
        status="UNRESOLVED_EXACT_LINK", exit_order_id=None,
    )
    with (
        patch.object(private_runtime, "TRADING_ACCOUNT_ID", ACCOUNT),
        patch.object(private_runtime, "resolve_exchange_position_close",
                     return_value=unresolved),
        patch.object(private_runtime, "release_position_capital_reservation") as release,
    ):
        private_runtime.reconcile_position_ownership(
            connection, position_list=[], now=1793790915618,
        )
    updates = [
        statement for statement in connection.statements
        if "UPDATE runtime.position_ownership" in statement
    ]
    assert len(updates) == 1
    # The last proven OPEN owner must remain visible to Exit/restart readers.
    # Keeping the slot alone is insufficient if the owner becomes CLOSED.
    assert "state='CLOSED'" not in updates[0]
    assert "close_link_status='UNRESOLVED_EXACT_LINK'" in updates[0]
    release.assert_not_called()
    assert not any("UPDATE runtime.exchange_position_slot_claims" in sql
                   for sql in connection.statements)
    assert not any("UPDATE runtime.capital_reservations" in sql
                   for sql in connection.statements)


def test_regression_does_not_weaken_exact_close_release() -> None:
    source = private_runtime.__file__
    with open(source, encoding="utf-8") as handle:
        text = handle.read()
    assert text.count("release_position_capital_reservation(") == 1
    exact = text.split("close_link_status='EXACT'", 1)[1]
    assert "release_position_capital_reservation(" in exact


@pytest.mark.parametrize("account", ("ta-main", "ta-sub"))
@pytest.mark.parametrize("new_fill", (False, True))
def test_rest_generation_cannot_overwrite_a_later_confirmed_fill(account, new_fill):
    class InventoryDB:
        def execute(self, statement, params):
            assert "created_at >= to_timestamp" in statement
            assert params == (account, 1793790909059)
            return Rows([(POSITION,)] if new_fill else [])

    with patch.object(private_runtime, "TRADING_ACCOUNT_ID", account):
        if new_fill:
            with pytest.raises(private_runtime.ExchangeReadUnavailable, match="OVERTAKEN"):
                private_runtime._require_inventory_not_overtaken_by_fill(
                    InventoryDB(), started_ms=1793790909059,
                )
        else:
            private_runtime._require_inventory_not_overtaken_by_fill(
                InventoryDB(), started_ms=1793790909059,
            )
