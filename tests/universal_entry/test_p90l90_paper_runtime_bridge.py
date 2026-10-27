"""P90L90 DB bridge: paper-only, restartable, explicit account identity."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalExit
from bybit_workbench.universal_entry.p90l90_paper_checkpoint import (
    P90L90PaperExitCheckpoint,
)
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime

D = Decimal
T0 = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)


class _Result:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _PaperConnection:
    def __init__(self):
        self.positions = {}
        self.events = []
        self.updates = 0

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, args=()):
        if "SELECT strategy_id,symbol,direction,entry_price,quantity" in sql:
            pos = self.positions.get(args[0])
            if pos is None:
                return _Result()
            return _Result((
                pos["strategy_id"], pos["symbol"], pos["direction"],
                pos["entry_price"], pos["quantity"], pos["notional"],
                pos["best"], pos["mfe"], pos["mae"], pos["payload"], pos["state"],
            ))
        if "UPDATE strategy_entry.paper_positions" in sql:
            pos = self.positions[args[-1]]
            if pos["state"] != "OPEN":
                return _Result()
            if "SET state='CLOSED'" in sql:
                (
                    pos["closed_at"], pos["exit_price"], pos["exit_reason"],
                    pos["best"], pos["mfe"], pos["mae"], pos["stop"],
                    pos["protected"], pos["gross"], pos["return_pct"],
                    raw_payload, _,
                ) = args
                pos["state"] = "CLOSED"
            else:
                (
                    pos["best"], pos["mfe"], pos["mae"], pos["stop"],
                    pos["protected"], raw_payload, _,
                ) = args
            pos["payload"] = json.loads(raw_payload)
            self.updates += 1
            return _Result((args[-1],))
        if "INSERT INTO strategy_entry.paper_position_events" in sql:
            self.events.append(args)
            return _Result()
        if "FROM strategy_entry.paper_orders" in sql:
            return _Result(rows=[])
        if "FROM strategy_entry.paper_positions" in sql:
            # Used by the generic public-tick route; P90 must be skipped.
            rows = []
            for ident, pos in self.positions.items():
                if pos["state"] == "OPEN":
                    rows.append((
                        ident, None, "PRIMARY", pos["direction"], T0,
                        pos["entry_price"], D("10"), 1, pos["notional"],
                        pos["quantity"], pos["best"], pos["mfe"], pos["mae"],
                        pos.get("stop"), pos.get("protected", False), False,
                        pos["payload"],
                    ))
            return _Result(rows=rows)
        raise AssertionError(f"unexpected SQL: {sql[:150]}")


def _add_position(db, *, account, strategy, position):
    checkpoint = P90L90PaperExitCheckpoint(
        trading_account_id=account,
        strategy_id=strategy,
        paper_position_id=position,
        symbol="LTCUSDT",
        exit_state=P90L90CausalExit(
            side="LONG", entry_price=D("100"), entry_sequence=0,
            state_bucket=int(T0.timestamp() // 300),
        ),
    )
    db.positions[position] = {
        "strategy_id": strategy, "symbol": "LTCUSDT", "direction": "LONG",
        "entry_price": D("100"), "quantity": D("0.1"), "notional": D("10"),
        "best": D("100"), "mfe": D("0"), "mae": D("0"), "state": "OPEN",
        "payload": {
            "exit_policy": {
                "local_zone_exit": {"operator": "P90L90_DYNAMIC_OPPOSITE_RAW_L53"}
            },
            "p90l90_exit_checkpoint": checkpoint.snapshot(),
        },
    }


def _advance(db, *, account, strategy, position, exec_id, seq, price, seconds):
    return PaperTradeRuntime(db).on_p90l90_native_trade(
        trading_account_id=account,
        strategy_id=strategy,
        paper_position_id=position,
        symbol="LTCUSDT",
        exec_id=exec_id,
        exchange_sequence=seq,
        event_at=T0 + timedelta(seconds=seconds),
        price=D(price),
        geometry_effective_at=None,
        candidate_opposite_raw_tp=None,
    )


def test_p90_paper_exit_persists_and_restores_after_runtime_restart() -> None:
    db = _PaperConnection()
    _add_position(db, account="ta-main", strategy="p90-main", position="paper-main")
    assert _advance(
        db, account="ta-main", strategy="p90-main", position="paper-main",
        exec_id="native-1", seq=10, price="100.6", seconds=1,
    ) is None
    assert db.positions["paper-main"]["stop"] == D("100.25")
    assert db.positions["paper-main"]["state"] == "OPEN"
    writes = db.updates
    assert _advance(
        db, account="ta-main", strategy="p90-main", position="paper-main",
        exec_id="native-1", seq=10, price="100.6", seconds=1,
    ) is None
    assert db.updates == writes
    closed = _advance(
        db, account="ta-main", strategy="p90-main", position="paper-main",
        exec_id="native-2", seq=11, price="100.2", seconds=2,
    )
    assert closed is not None and closed.reason == "PROTECTED_STOP"
    pos = db.positions["paper-main"]
    assert pos["state"] == "CLOSED"
    assert pos["exit_price"] == D("100.2")
    assert pos["payload"]["p90l90_modeled_economics"]["modeled_exchange_fill"] is False
    assert len(db.events) == 1
    assert json.loads(db.events[0][-1])["trading_account_id"] == "ta-main"


def test_two_accounts_isolated_and_generic_tick_does_not_touch_p90() -> None:
    db = _PaperConnection()
    _add_position(db, account="ta-main", strategy="p90-main", position="paper-main")
    _add_position(db, account="ta-sub", strategy="p90-sub", position="paper-sub")
    runtime = PaperTradeRuntime(db)
    runtime.on_public_trade(
        symbol="LTCUSDT", price=D("100.6"), observed_at=T0 + timedelta(seconds=1),
    )
    assert db.updates == 0  # generic R1 stop/target path must not run P90
    with pytest.raises(ValueError, match="trading_account_id mismatch"):
        _advance(
            db, account="ta-sub", strategy="p90-main", position="paper-main",
            exec_id="bad", seq=1, price="100.6", seconds=1,
        )
    assert db.updates == 0
    _advance(
        db, account="ta-main", strategy="p90-main", position="paper-main",
        exec_id="main-1", seq=1, price="100.6", seconds=1,
    )
    assert db.positions["paper-main"]["stop"] == D("100.25")
    assert db.positions["paper-sub"]["state"] == "OPEN"
    assert db.positions["paper-sub"]["payload"]["p90l90_exit_checkpoint"]["exit"]["stop"] == "90"


def test_generic_p90_paper_order_is_refused_before_any_db_write() -> None:
    db = _PaperConnection()
    card = SimpleNamespace(entry_policy=SimpleNamespace(to_dict=lambda: {
        "watch_policy": {"geometry": {"operator": "P90L90_RAW_H9_UNION_CROSSING"}}
    }))
    bundle = SimpleNamespace(card=card, activation=object(), exit_plan=object())
    evaluation = SimpleNamespace(execution_intent=object())
    with pytest.raises(RuntimeError, match="P90L90_PAPER_ENTRY_NOT_APPROVED"):
        PaperTradeRuntime(db).create_order(evaluation, bundle, now=T0)
    assert db.updates == 0
    assert not db.events
