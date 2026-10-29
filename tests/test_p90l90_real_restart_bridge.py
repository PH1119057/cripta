"""Append-only P90 REAL restart bridge identity and cursor proof."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from bybit_workbench.universal_entry.p90l90_real_restart_bridge import (
    verify_restart_bridge_for_position,
    write_p90_real_restart_bridge,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor

AT = datetime(2026, 10, 29, 21, 0, tzinfo=UTC)
SHA = "a" * 40
EPOCH = "p90-real-epoch-" + "b" * 40


class FakeDb:
    def __init__(self) -> None:
        self.info = SimpleNamespace(
            transaction_status=SimpleNamespace(name="INTRANS")
        )
        self.saved = None

    def execute(self, sql: str, params=()):
        if "FROM strategy_entry.p90l90_real_source_epochs" in sql:
            return SimpleNamespace(fetchone=lambda: (SHA, "ACTIVE"))
        if "INSERT INTO strategy_entry.p90l90_real_restart_bridges" in sql:
            self.saved = params
            return SimpleNamespace()
        if (
            "FROM strategy_entry.p90l90_real_restart_bridges" in sql
            and "symbol_cursors" in sql
        ):
            assert self.saved is not None
            return SimpleNamespace(fetchone=lambda: (
                self.saved[1], self.saved[2], self.saved[3],
                self.saved[4], self.saved[5], self.saved[6],
                json.loads(self.saved[7]), json.loads(self.saved[8]),
                json.loads(self.saved[9]),
            ))
        if "FROM strategy_entry.p90l90_real_restart_bridges" in sql:
            assert self.saved is not None
            return SimpleNamespace(fetchone=lambda: (
                self.saved[1], self.saved[2], self.saved[3],
                self.saved[4], json.loads(self.saved[8]),
            ))
        raise AssertionError("unexpected SQL: " + sql)


def test_restart_bridge_is_exact_append_only_position_cursor_identity() -> None:
    db = FakeDb()
    cursors = {
        symbol: TradeCursor(
            symbol=symbol, exec_id="exec-" + symbol, seq=100 + index,
            traded_at=AT + timedelta(milliseconds=index),
        )
        for index, symbol in enumerate(
            ("LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT")
        )
    }
    position = SimpleNamespace(
        strategy_position_id="position-ltc",
        trading_account_id="ta-main",
        symbol="LTCUSDT",
    )
    live = cursors["LTCUSDT"]
    mfe = SimpleNamespace(
        position_id=position.strategy_position_id,
        account_id=position.trading_account_id,
        symbol=position.symbol,
        source_commit=SHA,
        cursor_exec_id=live.exec_id,
        cursor_seq=live.seq,
        cursor_at=live.traded_at,
    )
    bridge = write_p90_real_restart_bridge(
        db,
        source_epoch_id=EPOCH,
        source_commit=SHA,
        mirror_serial=7,
        mirror_started_at=AT - timedelta(seconds=1),
        source_ws_ready_at=AT - timedelta(milliseconds=500),
        completed_at=AT + timedelta(seconds=45),
        symbol_cursors=cursors,
        position_mfe={position.strategy_position_id: (position, mfe)},
        account_generations={"ta-main": "gen-main", "ta-sub": "gen-sub"},
    )
    assert bridge.restart_id.startswith("p90-real-restart-")
    assert verify_restart_bridge_for_position(
        db,
        restart_id=bridge.restart_id,
        source_epoch_id=EPOCH,
        source_commit=SHA,
        mirror_serial=7,
        mirror_started_at=AT - timedelta(seconds=1),
        position=position,
        prior_mfe=mfe,
    ) is True
