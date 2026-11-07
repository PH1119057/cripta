"""Two physical PostgreSQL connections: protected Fill -> native POSTFILL -> MFE rollback.

Exact project PostfillExitPort, ExitStore, MFE ledger and account latch
operate against an isolated disposable *test-only* schema. The fake external
ExecutionPort records intent; it cannot send Bybit orders. This is a DB owner
boundary regression, NOT full signed Exchange/restart acceptance.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row
from test_p90l90_real_postfill_observation import _proof

from bybit_workbench.account_entry_safety import (
    account_entry_safety_ready,
    latch_account_entry_safety,
)
from bybit_workbench.universal_entry.transport_continuity import TradeCursor
from bybit_workbench.universal_exit.contracts import ExitActionKind, ExitEvaluationStatus
from bybit_workbench.universal_exit.p90l90_real_mfe import P90RealMfeUnverified
from bybit_workbench.universal_exit.p90l90_real_mfe_ledger import P90RealMfeLedger
from bybit_workbench.universal_exit.p90l90_real_postfill_port import P90RealPostfillExitPort
from bybit_workbench.universal_exit.storage import PostgresExitShadowStore

DSN = os.getenv("CRIPTA_TRADE_LIFECYCLE_TEST_DSN")
REQUIRED = os.getenv("CRIPTA_P90_POSTFILL_E2E_REQUIRED") == "1"


def require_disposable(connection):
    if connection.info.dbname != "cripta_integration":
        raise AssertionError("Production PostgreSQL forbidden")
    if connection.info.host not in ("", "/var/run/postgresql", "localhost", "127.0.0.1"):
        raise AssertionError("No remote PostgreSQL access permitted")
    assert connection.info.server_version >= 160000


class ScopedDB:
    def __init__(self, connection, schema):
        self.connection, self.schema = connection, schema

    @property
    def info(self):
        return self.connection.info

    def transaction(self):
        return self.connection.transaction()

    def execute(self, sql, params=()):
        sql = str(sql).replace("strategy_exit.", self.schema + ".")
        sql = sql.replace(
            "runtime.account_entry_safety_blocks",
            self.schema + ".account_entry_safety_blocks",
        )
        return self.connection.execute(sql, params)


class _TestExecution:
    def __init__(self, db):
        self.db = db

    def __call__(self, evaluation, position, plan):
        decision = evaluation.decision
        assert decision is not None
        assert position.exit_plan_fingerprint == plan.exit_plan_fingerprint
        self.db.execute(
            f"INSERT INTO {self.db.schema}.simulated_exit_requests"
            "(exit_decision_id,position_id,action_kind) VALUES (%s,%s,%s)"
            " ON CONFLICT (exit_decision_id) DO NOTHING",
            (decision.exit_decision_id, position.strategy_position_id, decision.action_kind.value),
        )


def count(db, table):
    return db.execute(f"SELECT count(*) AS n FROM {db.schema}.{table}").fetchone()["n"]


def postfill(db, position, plan, private, coverage):
    return P90RealPostfillExitPort(
        db, position_provider=lambda sym: ((position, plan),) if sym == position.symbol else (),
        exit_store=PostgresExitShadowStore(db),
        execution_port=_TestExecution(db),
    )(
        position=position, plan=plan, private=private, coverage=coverage,
        signed_fill_execution_ids=("synthetic-fill-id",),
        exact_entry_order_id="synthetic-original-order",
        received_at=position.fill_at + timedelta(milliseconds=5),
    )


def append_native(db, position, exec_id, seq, millis):
    t = position.fill_at + timedelta(milliseconds=millis)
    return P90RealMfeLedger(db).on_trade(
        position=position, source_commit="a" * 40,
        fact_id="synthetic-fact-" + exec_id,
        cursor=TradeCursor(symbol=position.symbol, exec_id=exec_id, seq=seq, traded_at=t),
        price=Decimal("100.2"), received_at=t + timedelta(milliseconds=1),
        source_refs=(f"synthetic:publicTrade:{position.symbol}:{exec_id}",),
        first_fill_forward_coverage_proven=True,
        native_continuity_proven=True,
    )


@pytest.mark.parametrize("defect", [None, "protection", "account", "plan", "quantity"])
def test_real_entry_completion_sql_requires_protected_exact_exit_owner(monkeypatch, defect):
    if not DSN:
        if REQUIRED:
            pytest.fail("Required disposable PG16 missing")
        pytest.skip("Requires explicitly configured disposable PG16")
    from test_first_fill_trailing_dispatch import runtime

    schema = "p90entryexit_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True) as admin:
        require_disposable(admin)
        admin.execute(f"CREATE SCHEMA {schema}")
        try:
            admin.execute(f"CREATE TABLE {schema}.trade_commands("
                          "command_id text PRIMARY KEY,command_type text,state text,"
                          "trading_account_id text,identity_status text,"
                          "strategy_execution_binding_id text,finished_at_epoch_ms bigint,"
                          "result_json text)")
            admin.execute(f"CREATE TABLE {schema}.position_ownership("
                          "position_id text PRIMARY KEY,entry_command_id text,state text,"
                          "identity_status text,trading_account_id text,"
                          "strategy_execution_binding_id text,actual_avg_fill numeric,"
                          "actual_qty numeric,fill_at timestamptz,"
                          "initial_protection_confirmed_at timestamptz,"
                          "exit_plan_fingerprint text,strategy_id text,strategy_version text,"
                          "strategy_config_fingerprint text)")
            admin.execute(f"CREATE TABLE {schema}.exit_plans("
                          "exit_plan_fingerprint text PRIMARY KEY,strategy_id text,"
                          "strategy_version text,strategy_config_fingerprint text)")
            admin.execute(f"INSERT INTO {schema}.trade_commands VALUES "
                          "('entry','entry','running','account','PROVEN','binding',NULL,NULL)")
            admin.execute(f"INSERT INTO {schema}.exit_plans VALUES "
                          "('plan','strategy','version','config')")
            admin.execute(f"INSERT INTO {schema}.position_ownership VALUES "
                          "('position','entry','OPEN','PROVEN',%s,'binding',100,%s,now(),%s,"
                          "'plan','strategy','version',%s)",
                          ("foreign" if defect == "account" else "account",
                           0 if defect == "quantity" else 1,
                           None if defect == "protection" else datetime.now(UTC),
                           "wrong" if defect == "plan" else "config"))
            admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO cripta")
            admin.execute(f"GRANT SELECT,UPDATE ON ALL TABLES IN SCHEMA {schema} TO cripta")
            with psycopg.connect(DSN) as c:
                require_disposable(c)
                c.execute("SET ROLE cripta")
                c.commit()
                class DB:
                    def execute(self, sql, params):
                        for table in ("trade_commands", "position_ownership", "exit_plans"):
                            sql = sql.replace("runtime." + table, schema + "." + table)
                            sql = sql.replace("strategy_entry." + table, schema + "." + table)
                        return c.execute(sql, params)
                    def commit(self):
                        c.commit()
                    def rollback(self):
                        c.rollback()
                monkeypatch.setattr(runtime, "TRADING_ACCOUNT_ID", "account")
                def complete():
                    runtime._complete_protected_p90_entry(
                        DB(), entry_command_id="entry", position_id="position",
                        entry_result={"actualProtection": {"verifiedFromExchange": True}},
                    )
                if defect:
                    with pytest.raises(runtime.ExchangeMutationBarrier):
                        complete()
                else:
                    complete()
                # Independent connection proves commit/rollback before Exit runs.
                assert admin.execute(f"SELECT state FROM {schema}.trade_commands").fetchone() == (
                    "running" if defect else "completed",
                )
        finally:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")


def test_two_connections_keep_protected_fill_and_exit_across_mfe_rollback():
    if not DSN:
        if REQUIRED:
            pytest.fail("Required disposable PG16 missing")
        pytest.skip("Requires explicitly configured disposable PG16")

    schema = "p90e2e_" + uuid4().hex
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as admin:
        require_disposable(admin)
        admin.execute(f"CREATE SCHEMA {schema}")
        try:
            ddl = (
                f"CREATE TABLE {schema}.simulated_protected_fill("
                "position_id text PRIMARY KEY,account_id text,"
                "protection_verified boolean NOT NULL)",
                f"CREATE TABLE {schema}.exit_observations("
                "observation_id text PRIMARY KEY,strategy_position_id text,"
                "exit_plan_fingerprint text,symbol text,event_kind text,event_at timestamptz,"
                "observed_at timestamptz,received_at timestamptz,"
                "attributes jsonb,source_refs jsonb)",
                f"CREATE TABLE {schema}.exit_decisions("
                "exit_decision_id text PRIMARY KEY,strategy_position_id text,strategy_id text,"
                "strategy_version text,strategy_config_fingerprint text,exit_plan_fingerprint text,"
                "observation_id text,rule_id text,rule_priority integer,repeat_policy text,"
                "action_kind text,requested_mutation jsonb,source_refs jsonb,"
                "decided_at timestamptz)",
                f"CREATE TABLE {schema}.shadow_evaluations("
                "evaluation_id text PRIMARY KEY,observation_id text,strategy_position_id text,"
                "exit_plan_fingerprint text,status text,reason text,matched_rule_ids jsonb,"
                "exit_decision_id text)",
                f"CREATE TABLE {schema}.simulated_exit_requests("
                "exit_decision_id text PRIMARY KEY,position_id text,action_kind text)",
                f"CREATE TABLE {schema}.account_entry_safety_blocks("
                "trading_account_id text,account_connector_id text,reason text,"
                "blocked_at timestamptz DEFAULT now(),"
                "PRIMARY KEY(trading_account_id,account_connector_id))",
            )
            for statement in ddl:
                admin.execute(statement)
            admin.execute(f"GRANT USAGE ON SCHEMA {schema} TO cripta")
            admin.execute(f"GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA {schema} TO cripta")
            with (
                psycopg.connect(DSN, row_factory=dict_row) as original,
                psycopg.connect(DSN, row_factory=dict_row) as observer,
            ):
                require_disposable(original)
                require_disposable(observer)
                assert original.info.backend_pid != observer.info.backend_pid
                for c in (original, observer):
                    c.execute("SET ROLE cripta")
                    c.commit()
                    assert c.execute("SELECT current_user").fetchone()["current_user"] == "cripta"
                    c.commit()
                owner = ScopedDB(original, schema)
                source = ScopedDB(observer, schema)
                p, plan, private, coverage = _proof()

                # Owner transaction A: native position + protection is
                # visible only AFTER COMMIT; no first Exit observation yet.
                with owner.transaction(), source.transaction():
                    owner.execute(
                        f"INSERT INTO {schema}.simulated_protected_fill VALUES (%s,%s,TRUE)",
                        (p.strategy_position_id, p.trading_account_id),
                    )
                    assert count(source, "simulated_protected_fill") == 0
                with source.transaction():
                    assert count(source, "simulated_protected_fill") == 1
                    assert count(source, "exit_observations") == 0

                # Owner transaction B: independent POSTFILL handoff.
                with owner.transaction():
                    e = postfill(owner, p, plan, private, coverage)
                    assert e.status is ExitEvaluationStatus.DECISION_CREATED
                    assert e.decision is not None
                    assert e.decision.action_kind is ExitActionKind.SET_TRAILING
                with source.transaction():
                    assert count(source, "exit_decisions") == 1
                    assert count(source, "simulated_exit_requests") == 1

                # Observer transaction C: immutable source MFE, then a
                # rejected same-sequence duplicate in another transaction.
                with source.transaction():
                    first = append_native(source, p, "first", 17, 3)
                assert first.cursor_exec_id == "first"
                with (
                    pytest.raises(P90RealMfeUnverified, match="NATIVE_ORDER_NOT_PROVEN"),
                    source.transaction(),
                ):
                    append_native(source, p, "second", 17, 3)
                with owner.transaction():
                    assert count(owner, "simulated_protected_fill") == 1
                    assert count(owner, "exit_decisions") == 1
                    assert count(owner, "simulated_exit_requests") == 1
                    assert count(owner, "exit_observations") == 2

                # Scoped Entry latch does not delete protected Exit records.
                with owner.transaction():
                    latch_account_entry_safety(
                        owner, trading_account_id=p.trading_account_id,
                        account_connector_id=private.connector_id,
                        reason="PRIVATE_ACCOUNT_MUTATION_SAFETY_BLOCKED",
                    )
                with source.transaction():
                    assert account_entry_safety_ready(
                        source, trading_account_id=p.trading_account_id,
                        account_connector_id=private.connector_id,
                    ) == (False, ("ACCOUNT_ENTRY_SAFETY_BLOCKED",))
                    assert count(source, "simulated_exit_requests") == 1
        finally:
            assert schema.startswith("p90e2e_") and len(schema) == 39
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
