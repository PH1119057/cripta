"""Disposable PostgreSQL 16 integration for the 24-hour HOT retention engine.

Never run against a production DB. A dedicated CI database is required, and
this fixture drops only its own disposable namespaces inside that database.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "market_hot_retention_ci", ROOT / "operations/monitoring/market_hot_retention.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.fixture()
def database():
    dsn = os.environ.get("CRIPTA_RETENTION_INTEGRATION_DSN")
    if not dsn:
        pytest.skip("Requires isolated disposable retention_ci PostgreSQL database")
    import psycopg

    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=4)
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert conn.execute("SELECT current_database()").fetchone()[0] == "cripta"
    for schema in ("research_context", "dispatcher_v2", "mayak_v2"):
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        conn.execute(f"CREATE SCHEMA {schema}")
    ddl = [
        """CREATE TABLE mayak_v2.snapshots(
            id bigint PRIMARY KEY, observed_at timestamptz NOT NULL,
            payload jsonb NOT NULL)""",
        """CREATE TABLE mayak_v2.observation_journal(
            snapshot_id bigint PRIMARY KEY REFERENCES mayak_v2.snapshots(id),
            observed_at timestamptz NOT NULL,
            price_breadth jsonb NOT NULL,
            money_breadth jsonb NOT NULL,
            direction_synchronization jsonb NOT NULL,
            data_quality jsonb NOT NULL,
            btc_context jsonb,
            eth_context jsonb,
            dispatcher_handoff jsonb)""",
        """CREATE TABLE mayak_v2.events(
            id bigint PRIMARY KEY,
            snapshot_id bigint REFERENCES mayak_v2.snapshots(id))""",
        """CREATE TABLE mayak_v2.shared_market_contexts(
            market_context_id text PRIMARY KEY,
            mayak_snapshot_id bigint REFERENCES mayak_v2.snapshots(id),
            observed_at timestamptz NOT NULL)""",
        """CREATE TABLE mayak_v2.coin_market_contexts(
            coin_context_id text PRIMARY KEY,
            mayak_snapshot_id bigint REFERENCES mayak_v2.snapshots(id),
            observed_at timestamptz NOT NULL)""",
        """CREATE TABLE dispatcher_v2.global_market_contexts(
            global_context_id text PRIMARY KEY,
            source_market_context_id text REFERENCES mayak_v2.shared_market_contexts(market_context_id),
            source_mayak_snapshot_id bigint REFERENCES mayak_v2.snapshots(id),
            observed_at timestamptz NOT NULL)""",
        """CREATE TABLE dispatcher_v2.coin_market_contexts(
            coin_context_id text PRIMARY KEY,
            global_context_id text REFERENCES dispatcher_v2.global_market_contexts(global_context_id),
            source_coin_context_id text REFERENCES mayak_v2.coin_market_contexts(coin_context_id),
            source_mayak_snapshot_id bigint REFERENCES mayak_v2.snapshots(id),
            observed_at timestamptz NOT NULL)""",
        """CREATE TABLE research_context.event_links(
            reference_id text PRIMARY KEY,
            observed_mayak_snapshot_id bigint REFERENCES mayak_v2.snapshots(id))""",
        """CREATE TABLE research_context.dispatcher_v2_event_links(
            reference_id text PRIMARY KEY,
            coin_context_id text REFERENCES dispatcher_v2.coin_market_contexts(coin_context_id),
            global_context_id text REFERENCES dispatcher_v2.global_market_contexts(global_context_id))""",
        """CREATE FUNCTION mayak_v2.reject_mutation() RETURNS trigger
           LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'IMMUTABLE_TEST'; END $$""",
        """CREATE FUNCTION dispatcher_v2.reject_mutation() RETURNS trigger
           LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'IMMUTABLE_TEST'; END $$""",
        """CREATE TRIGGER coin_market_contexts_immutable
           BEFORE DELETE OR UPDATE ON mayak_v2.coin_market_contexts
           FOR EACH ROW EXECUTE FUNCTION mayak_v2.reject_mutation()""",
        """CREATE TRIGGER shared_market_contexts_immutable
           BEFORE DELETE OR UPDATE ON mayak_v2.shared_market_contexts
           FOR EACH ROW EXECUTE FUNCTION mayak_v2.reject_mutation()""",
        """CREATE TRIGGER coin_market_contexts_immutable
           BEFORE DELETE OR UPDATE ON dispatcher_v2.coin_market_contexts
           FOR EACH ROW EXECUTE FUNCTION dispatcher_v2.reject_mutation()""",
        """CREATE TRIGGER global_market_contexts_immutable
           BEFORE DELETE OR UPDATE ON dispatcher_v2.global_market_contexts
           FOR EACH ROW EXECUTE FUNCTION dispatcher_v2.reject_mutation()""",
    ]
    for q in ddl:
        conn.execute(q)
    now = datetime.now(UTC)
    old, fresh = now - timedelta(hours=27), now - timedelta(hours=1)
    for id_, at in ((1, old), (2, old), (3, fresh)):
        conn.execute(
            "INSERT INTO mayak_v2.snapshots VALUES(%s,%s,%s::jsonb)",
            (id_, at, '{"money":"full"}'),
        )
        conn.execute(
            """INSERT INTO mayak_v2.observation_journal(
                snapshot_id,observed_at,price_breadth,money_breadth,
                direction_synchronization,data_quality,btc_context,
                eth_context,dispatcher_handoff)
               VALUES (%s,%s,'{"price":12}','{"money":7}','{"flow":1}',
                   '{"valid":true}','{"btc":1}','{"eth":1}','{"handoff":1}')""",
            (id_, at),
        )
        prefix = {1: "old", 2: "pinned", 3: "fresh"}[id_]
        conn.execute(
            "INSERT INTO mayak_v2.shared_market_contexts VALUES(%s,%s,%s)",
            ("ms-" + prefix, id_, at),
        )
        conn.execute(
            "INSERT INTO mayak_v2.coin_market_contexts VALUES(%s,%s,%s)",
            ("mc-" + prefix, id_, at),
        )
        conn.execute(
            "INSERT INTO dispatcher_v2.global_market_contexts VALUES(%s,%s,%s,%s)",
            ("dg-" + prefix, "ms-" + prefix, id_, at),
        )
        conn.execute(
            "INSERT INTO dispatcher_v2.coin_market_contexts VALUES(%s,%s,%s,%s,%s)",
            ("dc-" + prefix, "dg-" + prefix, "mc-" + prefix, id_, at),
        )
    conn.execute(
        "INSERT INTO research_context.dispatcher_v2_event_links VALUES('real-event','dc-pinned','dg-pinned')"
    )
    conn.execute("INSERT INTO research_context.event_links VALUES('decision',2)")
    try:
        yield conn, now
    finally:
        conn.close()


def _actor(monkeypatch):
    if sys.platform != "linux":
        pytest.skip("Production retention requires Linux")
    import pwd

    monkeypatch.setattr(MODULE.os, "geteuid", lambda: 123456)
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=123456))


def _names(conn, table, key):
    return {
        row[0] for row in conn.execute(
            f"SELECT {key} FROM {table} ORDER BY {key}"
        ).fetchall()
    }


def test_postgres_fk_audit_pin_fresh_and_compaction(database, monkeypatch):
    connection, now = database
    _actor(monkeypatch)
    MODULE.check_triggers(connection)
    result = MODULE.run_apply(connection, now - timedelta(hours=24), limit=100)
    assert result.mode == "APPLIED"
    assert all(result.rows[t] == 1 for t in MODULE.EXPECTED_TRIGGERS)
    for table, key, pref in (
        ("dispatcher_v2.coin_market_contexts", "coin_context_id", "dc"),
        ("mayak_v2.coin_market_contexts", "coin_context_id", "mc"),
        ("dispatcher_v2.global_market_contexts", "global_context_id", "dg"),
        ("mayak_v2.shared_market_contexts", "market_context_id", "ms"),
    ):
        assert _names(connection, table, key) == {pref + "-pinned", pref + "-fresh"}
    payloads = dict(connection.execute("SELECT id,payload FROM mayak_v2.snapshots"))
    assert payloads[1] == {"_cripta_hot_telemetry": "EXPIRED_24H"}
    assert payloads[2] == {"money": "full"}
    assert payloads[3] == {"money": "full"}
    journal = dict(connection.execute("SELECT snapshot_id,price_breadth FROM mayak_v2.observation_journal"))
    assert journal[1] == {}
    assert journal[2] == {"price": 12}
    assert journal[3] == {"price": 12}
    assert connection.execute("SELECT count(*) FROM research_context.event_links").fetchone()[0] == 1
    assert connection.execute("SELECT count(*) FROM research_context.dispatcher_v2_event_links").fetchone()[0] == 1
    MODULE.check_triggers(connection)
    second = MODULE.run_apply(connection, now - timedelta(hours=24), limit=100)
    assert not any(second.rows.values())  # Idempotent expiration.


def test_postgres_transaction_rollback_restores_data_and_triggers(database, monkeypatch):
    connection, now = database
    _actor(monkeypatch)
    connection.execute(
        """CREATE FUNCTION mayak_v2.abort_snapshot_update() RETURNS trigger
           LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'FORCED_ATOMIC_ROLLBACK'; END $$"""
    )
    connection.execute(
        """CREATE TRIGGER abort_snapshot_update BEFORE UPDATE ON mayak_v2.snapshots
           FOR EACH ROW EXECUTE FUNCTION mayak_v2.abort_snapshot_update()"""
    )
    with pytest.raises(Exception, match="FORCED_ATOMIC_ROLLBACK"):
        MODULE.run_apply(connection, now - timedelta(hours=24), limit=100)
    assert _names(connection, "dispatcher_v2.coin_market_contexts", "coin_context_id") == {
        "dc-old", "dc-pinned", "dc-fresh",
    }
    assert _names(connection, "mayak_v2.coin_market_contexts", "coin_context_id") == {
        "mc-old", "mc-pinned", "mc-fresh",
    }
    MODULE.check_triggers(connection)
