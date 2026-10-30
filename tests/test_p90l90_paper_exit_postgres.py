"""Disposable PostgreSQL 16 proof of the P90L90 account-scoped paper Exit rail.

No production database or Exchange access; fixture always rolls back.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.universal_entry.fingerprint import canonical_json
from bybit_workbench.universal_entry.p90l90_causal import P90L90CausalExit
from bybit_workbench.universal_entry.p90l90_paper_checkpoint import (
    P90L90PaperExitCheckpoint,
)
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="requires disposable PostgreSQL 16")
T0 = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)
D = Decimal


def test_p90l90_paper_exit_db_restart_and_account_isolation() -> None:
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_DISPOSABLE_P90L90"),
        conn.transaction(),
    ):
        positions = []
        for symbol, account, strategy in (
            ("LTCUSDT", "ta-disposable-main", "p90l90_legacy_main_ltc_inj"),
            ("DOTUSDT", "ta-disposable-sub", "p90l90_legacy_sub_dot_arb_apt"),
        ):
            pos_id = "paper-p90-qc-" + uuid4().hex
            checkpoint = P90L90PaperExitCheckpoint(
                trading_account_id=account,
                strategy_id=strategy,
                paper_position_id=pos_id,
                symbol=symbol,
                exit_state=P90L90CausalExit(
                    side="LONG", entry_price=D("100"),
                    entry_sequence=0,
                    state_bucket=int(T0.timestamp() // 300),
                ),
            )
            payload = {
                "exit_policy": {
                    "local_zone_exit": {
                        "operator": "P90L90_DYNAMIC_OPPOSITE_RAW_L53"
                    }
                },
                "p90l90_exit_checkpoint": checkpoint.snapshot(),
            }
            conn.execute(
                """INSERT INTO strategy_entry.paper_positions(
                      paper_position_id,paper_order_id,parent_position_id,
                      leg_type,strategy_activation_id,strategy_id,
                      strategy_version,strategy_config_fingerprint,
                      entry_plan_fingerprint,exit_plan_fingerprint,
                      signal_id,symbol,direction,state,opened_at,
                      entry_price,stake_usdt,leverage,notional_usdt,
                      quantity,best_price,payload
                   ) VALUES(
                      %s,NULL,NULL,'PRIMARY',%s,%s,
                      'p90l90-paper-fixture-v1',%s,
                      'entry-fixture','exit-fixture',
                      'signal-fixture',%s,'LONG','OPEN',%s,
                      100,10,1,10,0.1,100,%s::jsonb
                   )""",
                (
                    pos_id, "activation-fixture-"+pos_id,
                    strategy, "fingerprint-fixture-"+pos_id,
                    symbol, T0, canonical_json(payload),
                ),
            )
            positions.append((pos_id, symbol, account, strategy))
        main_id, main_symbol, main_account, main_strategy = positions[0]
        sub_id, _, sub_account, _ = positions[1]
        runtime = PaperTradeRuntime(conn)
        assert runtime.on_p90l90_native_trade(
            trading_account_id=main_account,
            strategy_id=main_strategy,
            paper_position_id=main_id,
            symbol=main_symbol,
            exec_id="e1", exchange_sequence=10,
            event_at=T0+timedelta(seconds=1),
            price=D("100.6"), geometry_effective_at=None,
            candidate_opposite_raw_tp=None,
        ) is None
        before = conn.execute(
            """SELECT active_stop_price,payload FROM
               strategy_entry.paper_positions WHERE paper_position_id=%s""",
            (main_id,),
        ).fetchone()
        assert before is not None
        assert D(before[0]) == D("100.25")
        restored = PaperTradeRuntime(conn)
        assert restored.on_p90l90_native_trade(
            trading_account_id=main_account,
            strategy_id=main_strategy,
            paper_position_id=main_id,
            symbol=main_symbol,
            exec_id="e1", exchange_sequence=10,
            event_at=T0+timedelta(seconds=1),
            price=D("100.6"), geometry_effective_at=None,
            candidate_opposite_raw_tp=None,
        ) is None
        duplicate = conn.execute(
            """SELECT payload FROM strategy_entry.paper_positions
                WHERE paper_position_id=%s""", (main_id,),
        ).fetchone()
        assert duplicate is not None and duplicate[0] == before[1]
        with pytest.raises(ValueError, match="trading_account_id mismatch"):
            restored.on_p90l90_native_trade(
                trading_account_id=sub_account,
                strategy_id=main_strategy,
                paper_position_id=main_id,
                symbol=main_symbol,
                exec_id="bad", exchange_sequence=11,
                event_at=T0+timedelta(seconds=2),
                price=D("100.7"), geometry_effective_at=None,
                candidate_opposite_raw_tp=None,
            )
        closed = restored.on_p90l90_native_trade(
            trading_account_id=main_account,
            strategy_id=main_strategy,
            paper_position_id=main_id,
            symbol=main_symbol,
            exec_id="e2", exchange_sequence=11,
            event_at=T0+timedelta(seconds=3),
            price=D("100.2"), geometry_effective_at=None,
            candidate_opposite_raw_tp=None,
        )
        assert closed is not None and closed.reason == "PROTECTED_STOP"
        rows = conn.execute(
            """SELECT state,exit_price,exit_reason,payload
                 FROM strategy_entry.paper_positions
                WHERE paper_position_id=%s""", (main_id,),
        ).fetchone()
        assert rows is not None and rows[0:3] == (
            "CLOSED", D("100.2"), "PROTECTED_STOP"
        )
        assert D(rows[3]["p90l90_modeled_economics"]["net_usdt"]) > D("0")
        events = conn.execute(
            """SELECT count(*) FROM strategy_entry.paper_position_events
                WHERE paper_position_id=%s AND event_type='CLOSED'""",
            (main_id,),
        ).fetchone()[0]
        assert events == 1
        sub = conn.execute(
            """SELECT state,active_stop_price FROM strategy_entry.paper_positions
               WHERE paper_position_id=%s""", (sub_id,),
        ).fetchone()
        assert sub == ("OPEN", None)
        raise RuntimeError("ROLLBACK_DISPOSABLE_P90L90")
