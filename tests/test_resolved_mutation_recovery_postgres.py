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
