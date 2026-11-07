"""Actual SECURITY DEFINER transaction/ACL/audit; disposable PostgreSQL only."""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

DSN = os.environ.get("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="explicit disposable PostgreSQL required")
ACCOUNT = "ta-853ac5fcd58185697396d33cd57459fa"
CONNECTOR = "ac-ec57819f875f1bfd929a382d6dc8a125"
ROLE = "cripta_account_entry_safety_recovery"


@pytest.fixture
def recovery_db():
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as admin:
        assert admin.info.dbname != "cripta", "production DB forbidden"
        schema = "mutation_recovery_" + uuid4().hex
        admin.execute(f"CREATE SCHEMA {schema}")
        definitions = {
            "account_entry_safety_blocks": "trading_account_id text,account_connector_id text,"
            "blocked_at timestamptz,reason text",
            "trading_accounts": "trading_account_id text,external_account_ref text,status text,"
            "environment text,venue_id text,account_type text",
            "account_connectors": "trading_account_id text,account_connector_id text,status text,"
            "retired_at timestamptz,adapter_id text,connector_kind text",
            "execution_gates": "mode text,enabled integer",
            "execution_permissions": "enabled boolean",
            "live_arm_sessions": "state text",
            "account_live_arm_sessions": "state text",
            "account_state_generations": "generation_id text,trading_account_id text,"
            "account_connector_id text,account_ref text,identity_status text,state text,"
            "positions_count integer,active_orders_count integer,completed_at timestamptz",
            "capital_reservations": "trading_account_id text,state text",
            "lifecycle_faults": "state text,severity text,exact_ids jsonb",
            "trade_commands": "command_id text,trading_account_id text,identity_status text,"
            "state text,command_type text,payload_json text",
            "position_ownership": "position_id text,trading_account_id text,identity_status text,"
            "state text,close_link_status text,bot_instance_id text,strategy_version text,"
            "fill_at timestamptz,closed_at timestamptz,actual_qty numeric,entry_command_id text,"
            "execution_ids jsonb,exit_execution_ids jsonb,exchange_order_ids jsonb,"
            "exit_order_ids jsonb,symbol text,side text",
            "executions": "exec_id text,order_id text,order_link_id text,trading_account_id text,"
            "account_connector_id text,identity_status text,symbol text,side text,exec_qty text",
            "exchange_order_history": "order_id text,order_link_id text,symbol text,"
            "side text,order_status text,payload_json text,trading_account_id text,"
            "account_connector_id text,identity_status text",
        }
        try:
            for table, columns in definitions.items():
                admin.execute(f"CREATE TABLE {schema}.{table} ({columns})")
            admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO cripta,{ROLE}")
            admin.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO cripta,{ROLE}")
            admin.execute(f"GRANT UPDATE,DELETE ON {schema}.account_entry_safety_blocks TO {ROLE}")
            migration = Path("operations/sql/20261105_resolved_mutation_recovery.sql").read_text()
            for original in ("control.", "runtime.", "identity.", "strategy_entry."):
                migration = migration.replace(original, schema + ".")
            admin.execute(migration)
            admin.execute(migration)  # Installer rerun preserves append-only schema/ACLs.
            now = datetime.now(UTC)
            blocked = now - timedelta(minutes=2)
            admin.execute(
                f"INSERT INTO {schema}.account_entry_safety_blocks VALUES(%s,%s,%s,%s)",
                (ACCOUNT, CONNECTOR, blocked, "PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED"),
            )
            admin.execute(
                f"INSERT INTO {schema}.trading_accounts VALUES "
                "(%s,'12345678','ACTIVE','MAINNET','venue-bybit','UNIFIED')",
                (ACCOUNT,),
            )
            admin.execute(
                f"INSERT INTO {schema}.account_connectors VALUES "
                "(%s,%s,'ACTIVE',NULL,'BYBIT_V5_PRIVATE','CEX_PRIVATE_RUNTIME')",
                (ACCOUNT, CONNECTOR),
            )
            admin.execute(
                f"INSERT INTO {schema}.execution_gates VALUES ('mainnet',0),('testnet',0)"
            )
            admin.execute(
                f"INSERT INTO {schema}.account_state_generations VALUES "
                "('generation',%s,%s,'BYBIT:UNIFIED','PROVEN','COMPLETE',0,0,%s)",
                (ACCOUNT, CONNECTOR, now),
            )
            for command, kind, payload in (
                ("entry-command", "entry", {}),
                ("owner-close", "close", {"strategy_position_id": "pos"}),
            ):
                admin.execute(
                    f"INSERT INTO {schema}.trade_commands VALUES "
                    "(%s,%s,'PROVEN','completed',%s,%s)",
                    (command, ACCOUNT, kind, json.dumps(payload)),
                )
            admin.execute(
                f"INSERT INTO {schema}.position_ownership VALUES "
                "('pos',%s,'PROVEN','CLOSED','EXACT','universal-entry',"
                "'2.0-p90l90-real-main-sub-20261028',%s,%s,10.5,'entry-command',"
                '\'["entry-exec"]\',\'["close-a","close-b"]\','
                "'[\"entry-order\"]','[\"close-order\"]','DOTUSDT','Buy')",
                (ACCOUNT, now - timedelta(minutes=3), now - timedelta(minutes=1)),
            )
            for identity, order, link, side, qty in (
                ("entry-exec", "entry-order", "entry-command", "Buy", "10.5"),
                ("close-a", "close-order", "owner-close", "Sell", "4.7"),
                ("close-b", "close-order", "owner-close", "Sell", "5.8"),
            ):
                admin.execute(
                    f"INSERT INTO {schema}.executions VALUES "
                    "(%s,%s,%s,%s,%s,'PROVEN','DOTUSDT',%s,%s)",
                    (identity, order, link, ACCOUNT, CONNECTOR, side, qty),
                )
            with psycopg.connect(DSN, autocommit=True) as worker:
                worker.execute("SET SESSION AUTHORIZATION cripta")

                def recover(**changes):
                    args = dict(
                        account=ACCOUNT,
                        connector=CONNECTOR,
                        release="a" * 40,
                        position="pos",
                        generation="generation",
                        uid="12345678",
                        blocked=blocked,
                        proof=datetime.now(UTC),
                        operator="owner",
                        request="test-owner-recovery",
                    )
                    args.update(changes)
                    with worker.transaction():
                        worker.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        return worker.execute(
                            f"SELECT {schema}.recover_closed_p90_mutation_safety("
                            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            tuple(args.values()),
                        ).fetchone()[0]

                yield admin, worker, schema, recover
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def test_exact_closed_recovery_records_immutable_audit_without_runtime_delete(recovery_db):
    import psycopg

    admin, worker, schema, recover = recovery_db
    # Historical CLOSED faults cannot veto this proof of resolved current state.
    admin.execute(
        f"INSERT INTO {schema}.lifecycle_faults VALUES ('OPEN','CRITICAL',%s::jsonb)",
        (json.dumps({"strategy_position_id": "pos", "trading_account_id": ACCOUNT}),),
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker.execute(f"DELETE FROM {schema}.account_entry_safety_blocks")
    assert recover()["status"] == "RECOVERED"
    assert recover()["status"] == "ALREADY_CLEAR"
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_entry_safety_blocks"
    ).fetchone() == (0,)
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_mutation_recovery_events"
    ).fetchone() == (1,)
    assert admin.execute(f"SELECT state FROM {schema}.position_ownership").fetchone() == ("CLOSED",)
    for command in ("DELETE FROM", "UPDATE", "TRUNCATE"):
        query = f"{command} {schema}.account_mutation_recovery_events" + (
            " SET operator='changed'" if command == "UPDATE" else ""
        )
        with pytest.raises(psycopg.errors.RaiseException, match="AUDIT_IMMUTABLE"):
            admin.execute(query)


@pytest.mark.parametrize(
    "failure",
    [
        "gate",
        "arm",
        "position",
        "stale",
        "pending",
        "foreign",
        "partial",
        "missing",
        "epoch",
        "uid",
        "fault",
        "unknown-fault",
    ],
)
def test_incomplete_or_changed_state_never_removes_latch(recovery_db, failure):
    import psycopg

    admin, _worker, schema, recover = recovery_db
    changes = {}
    if failure == "gate":
        admin.execute(f"UPDATE {schema}.execution_gates SET enabled=1 WHERE mode='mainnet'")
    elif failure == "arm":
        admin.execute(f"INSERT INTO {schema}.account_live_arm_sessions VALUES ('ACTIVE')")
    elif failure == "position":
        admin.execute(f"UPDATE {schema}.position_ownership SET state='OPEN'")
    elif failure == "stale":
        admin.execute(
            f"UPDATE {schema}.account_state_generations "
            "SET completed_at=now()-interval '60 seconds'"
        )
    elif failure == "pending":
        admin.execute(
            f"UPDATE {schema}.trade_commands SET state='running' WHERE command_id='owner-close'"
        )
    elif failure == "foreign":
        admin.execute(
            f"UPDATE {schema}.executions SET trading_account_id='foreign' WHERE exec_id='close-a'"
        )
    elif failure == "partial":
        admin.execute(f"UPDATE {schema}.executions SET exec_qty='1' WHERE exec_id='close-a'")
    elif failure == "missing":
        admin.execute(f"DELETE FROM {schema}.executions WHERE exec_id='close-b'")
    elif failure == "epoch":
        changes["blocked"] = datetime.now(UTC)
    elif failure == "uid":
        changes["uid"] = "777"
    else:
        fault = {"trading_account_id": ACCOUNT, "strategy_position_id": "unknown"}
        if failure == "unknown-fault":
            fault = {}
        admin.execute(
            f"INSERT INTO {schema}.lifecycle_faults VALUES ('OPEN','CRITICAL',%s::jsonb)",
            (json.dumps(fault),),
        )
    with pytest.raises(psycopg.errors.RaiseException, match="MUTATION_RECOVERY_"):
        recover(**changes)
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_entry_safety_blocks"
    ).fetchone() == (1,)
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_mutation_recovery_events"
    ).fetchone() == (0,)


def test_publisher_can_exclude_only_its_exact_unmaterialized_decision(recovery_db):
    from dataclasses import replace

    from test_p90l90_real_observation import _scope

    from bybit_workbench.exchange_position_identity import exchange_position_key
    from bybit_workbench.universal_exit.p90l90_real_pending import p90_real_exit_no_pending

    admin, worker, schema, _recover = recovery_db
    position, *_ = _scope(sub=True)
    position = replace(
        position,
        strategy_position_id="pos",
        trading_account_id=ACCOUNT,
        exchange_position_key=exchange_position_key(ACCOUNT, "DOTUSDT", 0),
    )
    for column in (
        "strategy_id text",
        "strategy_config_fingerprint text",
        "strategy_execution_binding_id text",
        "exchange_position_key text",
        "position_idx integer",
    ):
        admin.execute(f"ALTER TABLE {schema}.position_ownership ADD COLUMN {column}")
    admin.execute(
        f"UPDATE {schema}.position_ownership SET state='OPEN',strategy_id=%s,"
        "strategy_config_fingerprint=%s,strategy_execution_binding_id=%s,"
        "exchange_position_key=%s,position_idx=0",
        (
            position.strategy_id,
            position.strategy_config_fingerprint,
            position.strategy_execution_binding_id,
            position.exchange_position_key,
        ),
    )
    admin.execute(
        f"ALTER TABLE {schema}.trade_commands ADD COLUMN strategy_execution_binding_id text"
    )
    admin.execute(
        f"CREATE TABLE {schema}.exit_decisions(exit_decision_id text,"
        "strategy_position_id text,strategy_id text,strategy_version text,"
        "strategy_config_fingerprint text,exit_plan_fingerprint text)"
    )
    admin.execute(
        f"CREATE TABLE {schema}.execution_requests(execution_request_id text,"
        "exit_decision_id text,identity_status text,trading_account_id text,"
        "strategy_execution_binding_id text)"
    )
    admin.execute(
        f"CREATE TABLE {schema}.execution_dispatches(dispatch_id text,"
        "execution_request_id text,state text,command_id text)"
    )
    admin.execute(
        f"GRANT SELECT ON {schema}.exit_decisions,{schema}.execution_requests,"
        f"{schema}.execution_dispatches TO cripta"
    )
    admin.execute(
        f"INSERT INTO {schema}.exit_decisions VALUES('own','pos',%s,%s,%s,%s)",
        (
            position.strategy_id,
            position.strategy_version,
            position.strategy_config_fingerprint,
            position.exit_plan_fingerprint,
        ),
    )

    class ScopeDb:
        def execute(self, sql, args=()):
            return worker.execute(
                sql.replace("runtime.", schema + ".").replace("strategy_exit.", schema + "."), args
            )

    def ready(decision=None):
        with worker.transaction():
            return p90_real_exit_no_pending(
                ScopeDb(),
                position=position,
                trading_account_id=ACCOUNT,
                publishing_exit_decision_id=decision,
            )

    assert not ready()  # Ordinary signed reads still reject an unmaterialized decision.
    assert not ready("other")
    assert ready("own")  # The exact publisher does not deadlock on its own decision.
    admin.execute(
        f"INSERT INTO {schema}.exit_decisions SELECT 'competitor',"
        f"strategy_position_id,strategy_id,strategy_version,"
        f"strategy_config_fingerprint,exit_plan_fingerprint FROM {schema}.exit_decisions"
    )
    assert not ready("own")
    admin.execute(f"DELETE FROM {schema}.exit_decisions WHERE exit_decision_id='competitor'")
    admin.execute(
        f"INSERT INTO {schema}.execution_requests VALUES('request','own','PROVEN',%s,%s)",
        (ACCOUNT, position.strategy_execution_binding_id),
    )
    assert not ready("own")  # Already dispatched/uncertain work is never excluded.
    admin.execute(f"DELETE FROM {schema}.execution_requests")
    admin.execute(f"UPDATE {schema}.exit_decisions SET exit_plan_fingerprint='foreign'")
    assert not ready("own")


def _prepare_native_tp(recovery_db):
    """Original protected TP is fulfilled by Bybit, without a bot close command."""
    admin, _worker, schema, _recover = recovery_db
    admin.execute(f"ALTER TABLE {schema}.position_ownership ADD COLUMN position_idx integer")
    admin.execute(
        f"ALTER TABLE {schema}.position_ownership "
        "ADD COLUMN initial_protection_evidence jsonb"
    )
    admin.execute(f"ALTER TABLE {schema}.executions ADD COLUMN payload_json text")
    admin.execute(
        f"UPDATE {schema}.position_ownership SET actual_qty=16.51,"
        "position_idx=0,initial_protection_evidence=%s::jsonb,"
        "exit_order_ids=%s::jsonb,exit_execution_ids=%s::jsonb WHERE position_id='pos'",
        (
            json.dumps({"takeProfit": "0.5958", "stopLoss": "0.6661"}),
            json.dumps(["tp-order"]),
            json.dumps(["tp-exec"]),
        ),
    )
    admin.execute(
        f"UPDATE {schema}.executions SET exec_qty='16.51' "
        "WHERE exec_id='entry-exec'"
    )
    admin.execute(
        f"DELETE FROM {schema}.executions WHERE exec_id IN ('close-a','close-b')"
    )
    admin.execute(f"DELETE FROM {schema}.trade_commands WHERE command_id='owner-close'")
    history = {
        "stopOrderType": "TakeProfit", "createType": "CreateByTakeProfit",
        "orderType": "Market", "reduceOnly": "true", "closeOnTrigger": "true",
        "positionIdx": "0", "triggerPrice": "0.5958",
        "qty": "16.51", "cumExecQty": "16.51",
    }
    execution = {
        "execType": "Trade", "stopOrderType": "TakeProfit",
        "createType": "CreateByTakeProfit", "orderId": "tp-order",
        "orderLinkId": "",
    }
    admin.execute(
        f"INSERT INTO {schema}.exchange_order_history "
        "(order_id,order_link_id,symbol,side,order_status,payload_json,"
        "trading_account_id,account_connector_id,identity_status) "
        "VALUES('tp-order','','DOTUSDT','Sell','Filled',%s,%s,%s,'PROVEN')",
        (json.dumps(history), ACCOUNT, CONNECTOR),
    )
    # The base fixture is LONG: its Exchange-created TP Sell has no orderLinkId.
    # A separate SHORT regression exercises the production APT close shape.
    admin.execute(
        f"INSERT INTO {schema}.executions "
        "(exec_id,order_id,order_link_id,trading_account_id,"
        "account_connector_id,identity_status,symbol,side,exec_qty,payload_json) "
        "VALUES('tp-exec','tp-order','',%s,%s,'PROVEN','DOTUSDT',"
        "'Sell','16.51',%s)",
        (ACCOUNT, CONNECTOR, json.dumps(execution)),
    )


def test_native_takeprofit_recovery_records_original_exchange_provenance(recovery_db):
    admin, _worker, schema, recover = recovery_db
    _prepare_native_tp(recovery_db)
    result = recover()
    assert result["status"] == "RECOVERED"
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_entry_safety_blocks"
    ).fetchone() == (0,)
    row = admin.execute(
        f"SELECT evidence FROM {schema}.account_mutation_recovery_events"
    ).fetchone()[0]
    assert row["close_proof_mode"] == "EXCHANGE_NATIVE_TAKE_PROFIT"
    assert row["native_tp_order_id"] == "tp-order"
    assert row["exit_order_ids"] == ["tp-order"]
    assert row["exit_execution_ids"] == ["tp-exec"]
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.trade_commands "
        "WHERE command_type IN ('close','strategy_exit')"
    ).fetchone() == (0,)


def test_native_takeprofit_short_position_uses_opposite_buy_execution(recovery_db):
    admin, _worker, schema, recover = recovery_db
    _prepare_native_tp(recovery_db)
    admin.execute(f"UPDATE {schema}.position_ownership SET side='Sell'")
    admin.execute(
        f"UPDATE {schema}.executions SET side='Sell' WHERE exec_id='entry-exec'"
    )
    admin.execute(
        f"UPDATE {schema}.exchange_order_history SET side='Buy'"
    )
    admin.execute(
        f"UPDATE {schema}.executions SET side='Buy' WHERE exec_id='tp-exec'"
    )
    assert recover()["status"] == "RECOVERED"
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_mutation_recovery_events "
        "WHERE evidence->>'close_proof_mode'='EXCHANGE_NATIVE_TAKE_PROFIT'"
    ).fetchone() == (1,)


@pytest.mark.parametrize(
    "failure",
    [
        "history_missing", "history_unproven", "history_foreign",
        "history_not_filled", "history_stoploss", "history_wrong_origin",
        "history_wrong_price", "history_not_reduce", "history_not_close_trigger",
        "history_wrong_idx", "history_wrong_qty", "history_wrong_side",
        "execution_unproven", "execution_wrong_origin", "execution_linked",
        "execution_partial", "execution_missing", "original_tp_missing",
        "conflicting_bot_close",
    ],
)
def test_native_takeprofit_rejects_unknown_or_ambiguous_provenance(
    recovery_db, failure
):
    import psycopg

    admin, _worker, schema, recover = recovery_db
    _prepare_native_tp(recovery_db)
    order = f"{schema}.exchange_order_history"
    execution = f"{schema}.executions"
    if failure == "history_missing":
        admin.execute(f"DELETE FROM {order}")
    elif failure == "history_unproven":
        admin.execute(f"UPDATE {order} SET identity_status='UNPROVEN'")
    elif failure == "history_foreign":
        admin.execute(f"UPDATE {order} SET account_connector_id='foreign'")
    elif failure == "history_not_filled":
        admin.execute(f"UPDATE {order} SET order_status='New'")
    elif failure == "history_stoploss":
        admin.execute(
            f"UPDATE {order} SET payload_json=jsonb_set("
            "payload_json::jsonb,'{stopOrderType}',"
            "to_jsonb('StopLoss'::text))::text"
        )
    elif failure == "history_wrong_origin":
        admin.execute(
            f"UPDATE {order} SET payload_json=jsonb_set("
            "payload_json::jsonb,'{createType}',"
            "to_jsonb('CreateByUser'::text))::text"
        )
    elif failure == "history_wrong_price":
        admin.execute(
            f"UPDATE {order} SET payload_json=jsonb_set("
            "payload_json::jsonb,'{triggerPrice}',"
            "to_jsonb('0.5959'::text))::text"
        )
    elif failure == "history_not_reduce":
        admin.execute(
            f"UPDATE {order} SET payload_json=jsonb_set("
            "payload_json::jsonb,'{reduceOnly}',"
            "to_jsonb('false'::text))::text"
        )
    elif failure == "history_not_close_trigger":
        admin.execute(
            f"UPDATE {order} SET payload_json=jsonb_set("
            "payload_json::jsonb,'{closeOnTrigger}',"
            "to_jsonb('false'::text))::text"
        )
    elif failure == "history_wrong_idx":
        admin.execute(
            f"UPDATE {order} SET payload_json=jsonb_set("
            "payload_json::jsonb,'{positionIdx}',"
            "to_jsonb('1'::text))::text"
        )
    elif failure == "history_wrong_qty":
        admin.execute(
            f"UPDATE {order} SET payload_json=jsonb_set("
            "payload_json::jsonb,'{cumExecQty}',"
            "to_jsonb('15.5'::text))::text"
        )
    elif failure == "history_wrong_side":
        admin.execute(f"UPDATE {order} SET side='Buy'")
    elif failure == "execution_unproven":
        admin.execute(
            f"UPDATE {execution} SET identity_status='UNPROVEN' "
            "WHERE exec_id='tp-exec'"
        )
    elif failure == "execution_wrong_origin":
        admin.execute(
            f"UPDATE {execution} SET payload_json=jsonb_set("
            "payload_json::jsonb,'{stopOrderType}',"
            "to_jsonb('StopLoss'::text))::text WHERE exec_id='tp-exec'"
        )
    elif failure == "execution_linked":
        admin.execute(
            f"UPDATE {execution} SET order_link_id='forged-bot-command' "
            "WHERE exec_id='tp-exec'"
        )
    elif failure == "execution_partial":
        admin.execute(
            f"UPDATE {execution} SET exec_qty='12' WHERE exec_id='tp-exec'"
        )
    elif failure == "execution_missing":
        admin.execute(f"DELETE FROM {execution} WHERE exec_id='tp-exec'")
    elif failure == "original_tp_missing":
        admin.execute(
            f"UPDATE {schema}.position_ownership "
            "SET initial_protection_evidence='{}'::jsonb"
        )
    elif failure == "conflicting_bot_close":
        admin.execute(
            f"INSERT INTO {schema}.trade_commands VALUES "
            "('conflict',%s,'PROVEN','completed','close',%s)",
            (ACCOUNT, json.dumps({"strategy_position_id": "pos"})),
        )
    with pytest.raises(psycopg.errors.RaiseException, match="MUTATION_RECOVERY_NATIVE_TP_"):
        recover()
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_entry_safety_blocks"
    ).fetchone() == (1,)
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_mutation_recovery_events"
    ).fetchone() == (0,)
