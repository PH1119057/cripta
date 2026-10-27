"""Disposable PG16 proof: independent P90L90 no-order paper Entry -> Exit.

No production connection, Bybit access, approved real stake or live switch.
The fixture creates exact v2 Strategy bundles and account identities only
inside an intentionally rolled-back disposable CI transaction.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest

from bybit_workbench.universal_entry.contracts import (
    EntryEvaluation,
    EntryExecutionIntent,
    FrozenPolicy,
    StrategyActivation,
    StrategyAttempt,
    StrategySignal,
    TradeDirection,
)
from bybit_workbench.universal_entry.materializer import materialize_plans
from bybit_workbench.universal_entry.p90l90_legacy_strategy import MAIN_ID, SUB_ID
from bybit_workbench.universal_entry.p90l90_strategy_embargo import (
    StrategyHardStopEmbargoPolicy,
)
from bybit_workbench.universal_entry.p90l90_strategy_v2 import new_version_cards
from bybit_workbench.universal_entry.paper_runtime import PaperTradeRuntime
from bybit_workbench.universal_entry.runtime_loader import ActiveStrategyBundle
from bybit_workbench.universal_entry.storage import StrategyEntryStore

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="disposable PG16 DSN required")
T0 = datetime(2026, 10, 28, 1, 0, tzinfo=UTC)
D = Decimal
POLICY = StrategyHardStopEmbargoPolicy.parse({
    "enabled": True, "hardstop_count": 2,
    "rolling_hours": 48, "new_entry_pause_hours": 48,
    "close_existing_positions": False, "domain": "STRATEGY_VERSION",
})


def _source_entry(bundle, *, native_id):
    card, activation = bundle.card, bundle.activation
    symbol = card.symbols[0]
    signal = StrategySignal(
        signal_id="signal-" + native_id,
        strategy_id=card.strategy_id,
        strategy_version=card.strategy_version,
        strategy_config_fingerprint=card.strategy_config_fingerprint,
        entry_plan_fingerprint=bundle.entry_plan.entry_plan_fingerprint,
        strategy_activation_id=activation.activation_id,
        symbol=symbol, direction=TradeDirection.LONG,
        detected_at=T0+timedelta(seconds=1),
        source_refs=("bybit:" + native_id,),
        fact_id="derived-" + native_id,
    )
    attempt = StrategyAttempt(
        strategy_attempt_id="attempt-" + native_id,
        signal_id=signal.signal_id,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        strategy_config_fingerprint=signal.strategy_config_fingerprint,
        entry_plan_fingerprint=signal.entry_plan_fingerprint,
        strategy_activation_id=signal.strategy_activation_id,
        created_at=T0+timedelta(seconds=1),
    )
    intent = EntryExecutionIntent(
        strategy_attempt_id=attempt.strategy_attempt_id,
        signal_id=signal.signal_id,
        strategy_id=signal.strategy_id,
        strategy_version=signal.strategy_version,
        strategy_config_fingerprint=signal.strategy_config_fingerprint,
        entry_plan_fingerprint=signal.entry_plan_fingerprint,
        exit_plan_fingerprint=bundle.exit_plan.exit_plan_fingerprint,
        symbol=symbol, direction=TradeDirection.LONG,
        observed_at=T0+timedelta(seconds=1),
        payload=FrozenPolicy.from_mapping({
            "signal_fact": {
                "event_kind": "P90L90_RAW_H9_PREV7D_CROSSING",
                "attributes": {
                    "modeled_exchange_fill": False,
                    "trade_fact_id": "source-" + native_id,
                    "p90l90": {
                        "candidate_price": "100",
                        "state_effective_at": T0.isoformat(),
                    },
                },
            },
        }),
    )
    return EntryEvaluation(signal, attempt, None, None, intent, (), (), ())


def test_pg16_p90l90_open_checkpoint_replay_and_closed_net_no_paper_orders():
    with (
        psycopg.connect(DSN) as conn,
        pytest.raises(RuntimeError, match="ROLLBACK_P90_ENTRY"),
        conn.transaction(),
    ):
        assert conn.execute(
            "SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'"
        ).fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled"
        ).fetchone() == (0,)
        store = StrategyEntryStore(conn)
        cards = new_version_cards(
            approved_at=T0, policy_by_strategy_id={MAIN_ID: POLICY, SUB_ID: POLICY}
        )
        bundles = []
        for card in cards:
            account_id = "ta-p90-ci-" + uuid4().hex
            conn.execute(
                """INSERT INTO identity.trading_accounts(
                       trading_account_id,venue_id,account_kind,account_type,
                       environment,status,external_account_ref)
                   VALUES(%s,'venue-bybit',%s,'UNIFIED','MAINNET','ACTIVE',
                          '9000000000')""",
                (
                    account_id,
                    "MAIN" if card.strategy_id == MAIN_ID else "SUB",
                ),
            )
            activation = StrategyActivation(
                activation_id="activation-ci-" + uuid4().hex,
                strategy_id=card.strategy_id,
                strategy_version=card.strategy_version,
                strategy_config_fingerprint=card.strategy_config_fingerprint,
                enabled=True, enabled_at=T0,
                operator="CI", source="disposable-only",
            )
            plan, exit_plan = materialize_plans(card, activation)
            store.insert_strategy_card(card)
            store.insert_activation(activation, reason="test-disposable")
            store.insert_entry_plan(plan)
            store.insert_exit_plan(exit_plan)
            conn.execute(
                """INSERT INTO strategy_entry.strategy_execution_bindings(
                       strategy_execution_binding_id,strategy_activation_id,
                       execution_leg,execution_role,trading_account_id,
                       allocation_mode,binding_state,effective_from,
                       operator,source,reason)
                   VALUES(%s,%s,'PRIMARY','PRIMARY',%s,
                          'DEDICATED_STRATEGY','INACTIVE',%s,'CI',
                          'disposable-test','paper-only')""",
                ("binding-ci-"+uuid4().hex, activation.activation_id,
                 account_id, T0),
            )
            bundles.append(ActiveStrategyBundle(
                card, activation, plan, exit_plan, T0
            ))
        db = PaperTradeRuntime(conn)
        before = conn.execute(
            "SELECT count(*) FROM strategy_entry.paper_orders"
        ).fetchone()[0]
        ids = []
        for index, bundle in enumerate(bundles):
            native_id = "p90-native-" + str(index)
            evaluation = _source_entry(bundle, native_id=native_id)
            pos_id = db.create_p90l90_native_paper_entry(
                evaluation, bundle, exec_id=native_id,
                exchange_sequence=100 + index,
                event_at=T0+timedelta(seconds=1),
                native_trade_price=D("100"),
                raw_trade_fact_id="source-"+native_id,
            )
            assert pos_id is not None
            ids.append(pos_id)
            # Same publicTrade cannot open a duplicate after process restart.
            fresh = PaperTradeRuntime(conn)
            assert fresh.create_p90l90_native_paper_entry(
                evaluation, bundle, exec_id=native_id,
                exchange_sequence=100 + index,
                event_at=T0+timedelta(seconds=1),
                native_trade_price=D("100"),
                raw_trade_fact_id="source-"+native_id,
            ) is None
        assert len(set(ids)) == 2
        after = conn.execute(
            "SELECT count(*) FROM strategy_entry.paper_orders"
        ).fetchone()[0]
        assert before == after
        rows = conn.execute(
            """SELECT paper_position_id,paper_order_id,notional_usdt,
                      leverage,active_stop_price,payload
                 FROM strategy_entry.paper_positions
                WHERE paper_position_id IN (%s,%s)
                ORDER BY paper_position_id""",
            tuple(ids),
        ).fetchall()
        assert len(rows) == 2
        assert all(
            row[1] is None and row[2] == D("10")
            and row[3] == 1 and row[4] == D("90")
            and row[5]["modeled_exchange_fill"] is False for row in rows
        )
        # Restart and native-tick TRAIL035 close MAIN, SUB remains OPEN.
        main = bundles[0]
        account = conn.execute(
            """SELECT trading_account_id FROM
                 strategy_entry.strategy_execution_bindings
               WHERE strategy_activation_id=%s""",
            (main.activation.activation_id,),
        ).fetchone()[0]
        restarted = PaperTradeRuntime(conn)
        assert restarted.on_p90l90_native_trade(
            trading_account_id=account, strategy_id=main.card.strategy_id,
            paper_position_id=ids[0], symbol=main.card.symbols[0],
            exec_id="next-a", exchange_sequence=201,
            event_at=T0+timedelta(seconds=2), price=D("100.6"),
            geometry_effective_at=None, candidate_opposite_raw_tp=None,
        ) is None
        close = restarted.on_p90l90_native_trade(
            trading_account_id=account, strategy_id=main.card.strategy_id,
            paper_position_id=ids[0], symbol=main.card.symbols[0],
            exec_id="next-b", exchange_sequence=202,
            event_at=T0+timedelta(seconds=3), price=D("100.2"),
            geometry_effective_at=None, candidate_opposite_raw_tp=None,
        )
        assert close is not None and close.reason == "PROTECTED_STOP"
        positions = conn.execute(
            """SELECT state,payload FROM strategy_entry.paper_positions
                WHERE paper_position_id IN (%s,%s)
                ORDER BY paper_position_id""",
            tuple(ids),
        ).fetchall()
        assert sorted(x[0] for x in positions) == ["CLOSED", "OPEN"]
        closed_payload = next(payload for status, payload in positions
                              if status == "CLOSED")
        assert D(closed_payload["p90l90_modeled_economics"]["net_usdt"]) > 0
        assert closed_payload["p90l90_exit_checkpoint"][
            "trading_account_id"
        ] == account
        raise RuntimeError("ROLLBACK_P90_ENTRY")
