"""Disposable PostgreSQL proof for exact legacy pre-send account recovery."""
from __future__ import annotations

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
STRATEGY = "p90l90_legacy_sub_dot_arb_apt"
VERSION = "2.0-p90l90-real-main-sub-20261028"
BINDING = "binding-p90l90-real-sub-20261029-v3"
COMMAND = "ue-846cac3ccd79dca619a8f524cf0f8fff"
RESERVATION = "cap-0b36d05fbc457d9d3aa777d14727ca5f"
ROLE = "cripta_account_entry_safety_recovery"


@pytest.fixture
def recovery_db():
    import psycopg

    with psycopg.connect(DSN, autocommit=True) as admin:
        assert admin.info.dbname != "cripta", "production DB forbidden"
        schema = "pre_send_recovery_" + uuid4().hex
        admin.execute(f"CREATE SCHEMA {schema}")
        definitions = {
            "account_entry_safety_blocks":
                "trading_account_id text,account_connector_id text,"
                "blocked_at timestamptz,reason text",
            "trading_accounts":
                "trading_account_id text,external_account_ref text,status text,"
                "environment text,venue_id text,account_type text",
            "account_connectors":
                "trading_account_id text,account_connector_id text,status text,"
                "retired_at timestamptz,adapter_id text,connector_kind text",
            "execution_gates": "mode text,enabled integer",
            "execution_permissions": "enabled boolean",
            "live_arm_sessions": "state text",
            "account_live_arm_sessions": "state text",
            "account_state_generations":
                "generation_id text,trading_account_id text,"
                "account_connector_id text,account_ref text,identity_status text,"
                "state text,positions_count integer,active_orders_count integer,"
                "completed_at timestamptz",
            "trade_commands":
                "command_id text,command_type text,symbol text,payload_json text,"
                "state text,requested_at_epoch_ms bigint,started_at_epoch_ms bigint,"
                "finished_at_epoch_ms bigint,result_json text,error text,"
                "strategy_execution_binding_id text,execution_leg text,"
                "trading_account_id text,identity_status text",
            "capital_reservations":
                "reservation_id text,account_ref text,strategy_id text,"
                "strategy_version text,strategy_config_fingerprint text,"
                "entry_plan_fingerprint text,signal_id text,strategy_attempt_id text,"
                "requested_amount numeric,amount_currency text,"
                "capacity_snapshot_id text,capacity_observed_at timestamptz,"
                "capacity_available_at_reservation numeric,"
                "pre_dispatch_expires_at timestamptz,state text,state_reason text,"
                "exchange_commitment_ref text,exchange_commitment_at timestamptz,"
                "created_at timestamptz,updated_at timestamptz,"
                "strategy_position_id text,strategy_execution_binding_id text,"
                "execution_leg text,trading_account_id text,identity_status text",
            "position_ownership":
                "position_id text,trading_account_id text,state text,"
                "entry_command_id text",
            "executions":
                "exec_id text,order_link_id text,trading_account_id text,"
                "identity_status text",
            "exchange_order_history":
                "order_id text,order_link_id text,trading_account_id text,"
                "identity_status text",
            "hot_orders":
                "order_id text,order_link_id text,trading_account_id text,"
                "identity_status text",
            "lifecycle_faults":
                "state text,severity text,exact_ids jsonb",
        }
        try:
            for table, columns in definitions.items():
                admin.execute(f"CREATE TABLE {schema}.{table} ({columns})")
            admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO cripta,{ROLE}")
            admin.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO cripta,{ROLE}")
            admin.execute(
                f"GRANT UPDATE,DELETE ON {schema}.account_entry_safety_blocks TO {ROLE}"
            )
            migration = Path(
                "operations/sql/20261117_p90_pre_send_account_recovery.sql"
            ).read_text(encoding="utf-8")
            for original in ("control.", "runtime.", "identity.", "strategy_entry."):
                migration = migration.replace(original, schema + ".")
            admin.execute(migration)
            admin.execute(migration)

            now = datetime.now(UTC)
            blocked = now - timedelta(minutes=2)
            finished_ms = int(blocked.timestamp() * 1000) - 20
            requested_ms = finished_ms - 4500
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
            payload = {
                "strategy_id": STRATEGY,
                "strategy_version": VERSION,
                "capital_reservation_id": RESERVATION,
            }
            admin.execute(
                f"INSERT INTO {schema}.trade_commands VALUES "
                "(%s,'entry','APTUSDT',%s,'failed',%s,%s,%s,'{}',"
                "'PRE_MUTATION_SAFETY_BLOCK:PreMutationSafetyBlock',%s,'PRIMARY',%s,'PROVEN')",
                (
                    COMMAND, json.dumps(payload), requested_ms, requested_ms + 10,
                    finished_ms, BINDING, ACCOUNT,
                ),
            )
            admin.execute(
                f"INSERT INTO {schema}.capital_reservations VALUES "
                "(%s,'BYBIT:UNIFIED',%s,%s,'cfg','plan','sig','attempt',10,'USDT',"
                "'snapshot',%s,20,%s,'RELEASED',"
                "'ENTRY_NO_MUTATION:PRE_MUTATION_SAFETY_BLOCK:PreMutationSafetyBlock',"
                "NULL,NULL,%s,%s,NULL,%s,'PRIMARY',%s,'PROVEN')",
                (
                    RESERVATION, STRATEGY, VERSION, blocked - timedelta(seconds=10),
                    blocked - timedelta(seconds=5), blocked - timedelta(seconds=5),
                    blocked - timedelta(milliseconds=10), BINDING, ACCOUNT,
                ),
            )

            with psycopg.connect(DSN, autocommit=True) as worker:
                worker.execute("SET SESSION AUTHORIZATION cripta")

                def recover(**changes):
                    args = dict(
                        account=ACCOUNT,
                        connector=CONNECTOR,
                        release="a" * 40,
                        command=COMMAND,
                        generation="generation",
                        uid="12345678",
                        blocked=blocked,
                        proof=datetime.now(UTC),
                        operator="owner",
                        request="test-pre-send-recovery",
                    )
                    args.update(changes)
                    with worker.transaction():
                        worker.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                        return worker.execute(
                            f"SELECT {schema}.recover_p90_pre_send_account_safety("
                            "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            tuple(args.values()),
                        ).fetchone()[0]

                yield admin, worker, schema, recover
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def test_exact_no_mutation_recovery_is_audited_and_idempotent(recovery_db):
    import psycopg

    admin, worker, schema, recover = recovery_db
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        worker.execute(f"DELETE FROM {schema}.account_entry_safety_blocks")

    result = recover()
    assert result["status"] == "RECOVERED"
    assert result["command_id"] == COMMAND
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_entry_safety_blocks"
    ).fetchone() == (0,)
    row = admin.execute(
        f"SELECT command_id,reservation_id,evidence "
        f"FROM {schema}.pre_send_account_recovery_events"
    ).fetchone()
    assert row[0:2] == (COMMAND, RESERVATION)
    assert row[2]["exchange_effect_observed"] is False
    assert row[2]["reservation_state"] == "RELEASED"

    assert recover(request="test-pre-send-recovery-2")["status"] == "ALREADY_CLEAR"
    for statement in ("DELETE FROM", "UPDATE", "TRUNCATE"):
        query = f"{statement} {schema}.pre_send_account_recovery_events"
        if statement == "UPDATE":
            query += " SET operator='changed'"
        with pytest.raises(psycopg.errors.RaiseException, match="AUDIT_IMMUTABLE"):
            admin.execute(query)


@pytest.mark.parametrize(
    "failure",
    [
        "gate", "stale", "epoch", "uid", "pending", "reservation",
        "commitment", "candidate", "execution", "history", "hot_order",
        "position", "fault", "result",
    ],
)
def test_unknown_or_changed_state_never_clears_latch(recovery_db, failure):
    import psycopg

    admin, _worker, schema, recover = recovery_db
    changes = {}
    if failure == "gate":
        admin.execute(
            f"UPDATE {schema}.execution_gates SET enabled=1 WHERE mode='mainnet'"
        )
    elif failure == "stale":
        admin.execute(
            f"UPDATE {schema}.account_state_generations "
            "SET completed_at=now()-interval '60 seconds'"
        )
    elif failure == "epoch":
        changes["blocked"] = datetime.now(UTC)
    elif failure == "uid":
        changes["uid"] = "777"
    elif failure == "pending":
        admin.execute(
            f"INSERT INTO {schema}.trade_commands "
            "SELECT 'pending',command_type,symbol,payload_json,'running',"
            "requested_at_epoch_ms,started_at_epoch_ms,NULL,'{}',NULL,"
            "strategy_execution_binding_id,execution_leg,trading_account_id,"
            "identity_status FROM {schema}.trade_commands WHERE command_id=%s",
            (COMMAND,),
        )
    elif failure == "reservation":
        admin.execute(
            f"UPDATE {schema}.capital_reservations SET state='RESERVED'"
        )
    elif failure == "commitment":
        admin.execute(
            f"UPDATE {schema}.capital_reservations "
            "SET exchange_commitment_ref='order-unknown'"
        )
    elif failure == "candidate":
        admin.execute(
            f"INSERT INTO {schema}.trade_commands "
            "SELECT 'other-candidate',command_type,symbol,payload_json,state,"
            "requested_at_epoch_ms,started_at_epoch_ms,finished_at_epoch_ms,"
            "result_json,error,strategy_execution_binding_id,execution_leg,"
            "trading_account_id,identity_status "
            f"FROM {schema}.trade_commands WHERE command_id=%s",
            (COMMAND,),
        )
    elif failure == "execution":
        admin.execute(
            f"INSERT INTO {schema}.executions VALUES "
            "('exec',%s,%s,'PROVEN')",
            (COMMAND, ACCOUNT),
        )
    elif failure == "history":
        admin.execute(
            f"INSERT INTO {schema}.exchange_order_history VALUES "
            "('order',%s,%s,'PROVEN')",
            (COMMAND, ACCOUNT),
        )
    elif failure == "hot_order":
        admin.execute(
            f"INSERT INTO {schema}.hot_orders VALUES "
            "('order',%s,%s,'PROVEN')",
            (COMMAND, ACCOUNT),
        )
    elif failure == "position":
        admin.execute(
            f"INSERT INTO {schema}.position_ownership VALUES "
            "('pos',%s,'OPEN',%s)",
            (ACCOUNT, COMMAND),
        )
    elif failure == "fault":
        admin.execute(
            f"INSERT INTO {schema}.lifecycle_faults VALUES "
            "('OPEN','CRITICAL',%s::jsonb)",
            (json.dumps({"trading_account_id": ACCOUNT}),),
        )
    elif failure == "result":
        admin.execute(
            f"UPDATE {schema}.trade_commands SET result_json=%s",
            (json.dumps({"result": {"orderId": "observed"}}),),
        )

    with pytest.raises(psycopg.errors.RaiseException, match="PRE_SEND_RECOVERY_"):
        recover(**changes)
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.account_entry_safety_blocks"
    ).fetchone() == (1,)
    assert admin.execute(
        f"SELECT count(*) FROM {schema}.pre_send_account_recovery_events"
    ).fetchone() == (0,)
