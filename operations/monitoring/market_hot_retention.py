#!/usr/bin/env python3
"""Bounded 24h retention for *recomputable* MAYAK/Dispatcher full context.

Scope/actor contract:
  SCRIPT         operations/monitoring/market_hot_retention.py
  ACTOR          postgres (Unix UID and PostgreSQL session_user) for --apply;
                 any SELECT-capable role for --check
  READ_PATHS     installed script, PostgreSQL local socket
  WRITE_PATHS    NONE on filesystem
  DB_WRITES      only five known full-telemetry tables in this module;
                 no audit/identity/execution/compact-minute/history tables
  CREATE/RENAME/DELETE CHILDREN   NONE on filesystem
  SYSTEMD SANDBOX  ProtectSystem=strict; no filesystem write permissions
  ROLLBACK       atomic PostgreSQL transaction (named trigger DDL included)

No triggers are disabled globally or outside the transaction. Foreign-key
constraints remain enabled throughout. A referenced context or an event-linked
snapshot is *pinned* and exempt from raw expiration; compact historical facts
remain in place. Physical pg_total_relation_size does not necessarily drop on
DELETE/UPDATE; autovacuum can reuse the pages. No VACUUM FULL is run here.

Do not install/enable --apply until the release, canonical R11 identity and
compact-consumer gates, migration/backup gates, and production canary are PASS.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any

RETENTION_HOURS = 24
DEFAULT_BATCH_LIMIT = 1000
MAX_BATCH_LIMIT = 5000
EXPECTED_TRIGGERS = {
    "dispatcher_v2.coin_market_contexts": "coin_market_contexts_immutable",
    "mayak_v2.coin_market_contexts": "coin_market_contexts_immutable",
    "dispatcher_v2.global_market_contexts": "global_market_contexts_immutable",
    "mayak_v2.shared_market_contexts": "shared_market_contexts_immutable",
}
EXPIRED_PAYLOAD = '{"_cripta_hot_telemetry":"EXPIRED_24H"}'

# Execution ordering is a FK topological order, from child toward parent.
# Never remove an event-linked context from the live table; its exact record
# remains the source of the original audit evidence.
DELETES: tuple[tuple[str, str], ...] = (
    (
        "dispatcher_v2.coin_market_contexts",
        "NOT EXISTS (SELECT 1 FROM research_context.dispatcher_v2_event_links e "
        "WHERE e.coin_context_id=x.coin_context_id)",
    ),
    (
        "mayak_v2.coin_market_contexts",
        "NOT EXISTS (SELECT 1 FROM dispatcher_v2.coin_market_contexts d "
        "WHERE d.source_coin_context_id=x.coin_context_id)",
    ),
    (
        "dispatcher_v2.global_market_contexts",
        "NOT EXISTS (SELECT 1 FROM dispatcher_v2.coin_market_contexts d "
        "WHERE d.global_context_id=x.global_context_id) "
        "AND NOT EXISTS (SELECT 1 FROM research_context.dispatcher_v2_event_links e "
        "WHERE e.global_context_id=x.global_context_id)",
    ),
    (
        "mayak_v2.shared_market_contexts",
        "NOT EXISTS (SELECT 1 FROM dispatcher_v2.global_market_contexts d "
        "WHERE d.source_market_context_id=x.market_context_id)",
    ),
)
PKS = {
    "dispatcher_v2.coin_market_contexts": "coin_context_id",
    "mayak_v2.coin_market_contexts": "coin_context_id",
    "dispatcher_v2.global_market_contexts": "global_context_id",
    "mayak_v2.shared_market_contexts": "market_context_id",
}

# The source snapshot can have durable children (state transitions, compact
# minute facts, observation summaries). Preserve its stable ID and objective
# scalar attributes, expiring only its large derived payload if it is not
# needed for an event-linked decision or a pinned raw context.
PINNED_SNAPSHOT = (
    "EXISTS (SELECT 1 FROM research_context.event_links e "
    "WHERE e.observed_mayak_snapshot_id=x.id) OR "
    "EXISTS (SELECT 1 FROM mayak_v2.events e WHERE e.snapshot_id=x.id) OR "
    "EXISTS (SELECT 1 FROM dispatcher_v2.coin_market_contexts d "
    "WHERE d.source_mayak_snapshot_id=x.id) OR "
    "EXISTS (SELECT 1 FROM dispatcher_v2.global_market_contexts d "
    "WHERE d.source_mayak_snapshot_id=x.id) OR "
    "EXISTS (SELECT 1 FROM mayak_v2.coin_market_contexts d "
    "WHERE d.mayak_snapshot_id=x.id) OR "
    "EXISTS (SELECT 1 FROM mayak_v2.shared_market_contexts d "
    "WHERE d.mayak_snapshot_id=x.id)"
)
PINNED_JOURNAL = PINNED_SNAPSHOT.replace("x.id", "x.snapshot_id")


@dataclass(frozen=True)
class Result:
    mode: str
    cutoff_utc: str
    rows: dict[str, int]
    database_bytes: int


def delete_sql(table: str, predicates: str) -> str:
    if table not in PKS:
        raise ValueError("RETENTION_TABLE_NOT_ALLOWLISTED")
    pk = PKS[table]
    return (
        f"WITH stale AS (SELECT x.{pk} FROM {table} x "
        "WHERE x.observed_at < %s AND " + predicates + " "
        f"ORDER BY x.observed_at, x.{pk} LIMIT %s FOR UPDATE SKIP LOCKED) "
        f"DELETE FROM {table} target USING stale WHERE target.{pk}=stale.{pk}"
    )


def compact_snapshot_sql() -> str:
    return (
        "WITH stale AS (SELECT x.id FROM mayak_v2.snapshots x "
        "WHERE x.observed_at < %s AND x.payload <> %s::jsonb "
        f"AND NOT ({PINNED_SNAPSHOT}) "
        "ORDER BY x.observed_at,x.id LIMIT %s FOR UPDATE SKIP LOCKED) "
        "UPDATE mayak_v2.snapshots target SET payload=%s::jsonb "
        "FROM stale WHERE target.id=stale.id"
    )


def compact_journal_sql() -> str:
    return (
        "WITH stale AS (SELECT x.snapshot_id FROM mayak_v2.observation_journal x "
        "WHERE x.observed_at < %s "
        "AND (x.price_breadth <> '{}'::jsonb OR x.money_breadth <> '{}'::jsonb "
        "OR x.direction_synchronization <> '{}'::jsonb "
        "OR x.data_quality <> '{}'::jsonb OR x.btc_context IS NOT NULL "
        "OR x.eth_context IS NOT NULL OR x.dispatcher_handoff IS NOT NULL) "
        f"AND NOT ({PINNED_JOURNAL}) "
        "ORDER BY x.observed_at,x.snapshot_id LIMIT %s FOR UPDATE SKIP LOCKED) "
        "UPDATE mayak_v2.observation_journal target "
        "SET price_breadth='{}'::jsonb,money_breadth='{}'::jsonb, "
        "direction_synchronization='{}'::jsonb,data_quality='{}'::jsonb, "
        "btc_context=NULL,eth_context=NULL,dispatcher_handoff=NULL "
        "FROM stale WHERE target.snapshot_id=stale.snapshot_id"
    )


def check_triggers(connection: Any) -> None:
    expected = set(EXPECTED_TRIGGERS.items())
    tables = tuple(EXPECTED_TRIGGERS)
    # Regclass strings are static, never passed from user input.
    params = tuple(tables)
    found = connection.execute(
        """SELECT c.oid::regclass::text,t.tgname,t.tgenabled
        FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        WHERE NOT t.tgisinternal AND c.oid::regclass::text=ANY(%s::text[])""",
        (list(params),),
    ).fetchall()
    actual = {(str(t), str(name)) for t, name, enabled in found if enabled == 'O'}
    if actual != expected or len(found) != len(expected):
        raise RuntimeError("RETENTION_IMMUTABLE_TRIGGER_CONTRACT_CHANGED")


def collect_check(connection: Any, cutoff: datetime) -> Result:
    check_triggers(connection)
    counts: dict[str, int] = {}
    for table, predicate in DELETES:
        # Planner counts candidates that are not directly referenced.
        counts[table] = int(connection.execute(
            f"SELECT count(*) FROM {table} x WHERE x.observed_at < %s AND {predicate}",
            (cutoff,),
        ).fetchone()[0])
    counts["mayak_v2.snapshots.payload"] = int(connection.execute(
        "SELECT count(*) FROM mayak_v2.snapshots x WHERE x.observed_at < %s "
        f"AND x.payload <> %s::jsonb AND NOT ({PINNED_SNAPSHOT})",
        (cutoff, EXPIRED_PAYLOAD),
    ).fetchone()[0])
    counts["mayak_v2.observation_journal.context"] = int(connection.execute(
        "SELECT count(*) FROM mayak_v2.observation_journal x WHERE x.observed_at < %s "
        "AND (x.price_breadth <> '{}'::jsonb OR x.money_breadth <> '{}'::jsonb "
        "OR x.direction_synchronization <> '{}'::jsonb OR x.data_quality <> '{}'::jsonb "
        "OR x.btc_context IS NOT NULL OR x.eth_context IS NOT NULL "
        "OR x.dispatcher_handoff IS NOT NULL) "
        f"AND NOT ({PINNED_JOURNAL})",
        (cutoff,),
    ).fetchone()[0])
    nbytes = int(connection.execute("SELECT pg_database_size(current_database())").fetchone()[0])
    return Result("CHECK", cutoff.isoformat(), counts, nbytes)


def run_apply(connection: Any, cutoff: datetime, limit: int) -> Result:
    if sys.platform != "linux":
        raise RuntimeError("RETENTION_LINUX_REQUIRED")
    import pwd

    if os.geteuid() != pwd.getpwnam("postgres").pw_uid:
        raise RuntimeError("RETENTION_POSTGRES_UNIX_ACTOR_REQUIRED")
    result: dict[str, int] = {}
    # All DDL, DELETE and UPDATE are in one transaction. A failed operation
    # automatically restores trigger state on ROLLBACK, even if Python exits.
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '3000ms'")
        connection.execute("SET LOCAL statement_timeout = '60000ms'")
        roles = connection.execute("SELECT session_user,current_user,current_database()").fetchone()
        if roles != ("postgres", "postgres", "cripta"):
            raise RuntimeError("RETENTION_POSTGRES_DB_ROLE_REQUIRED")
        got_lock = connection.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext('cripta-market-hot-retention-v1'))"
        ).fetchone()[0]
        if got_lock is not True:
            raise RuntimeError("RETENTION_JOB_ALREADY_RUNNING")
        check_triggers(connection)
        # Suppress *only* the four named immutable-ROW triggers in this
        # transaction. FK/constraint triggers stay enabled at all times.
        for table, trigger in EXPECTED_TRIGGERS.items():
            connection.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
        for table, predicate in DELETES:
            result[table] = int(connection.execute(delete_sql(table, predicate), (cutoff, limit)).rowcount)
        result["mayak_v2.snapshots.payload"] = int(connection.execute(
            compact_snapshot_sql(), (cutoff, EXPIRED_PAYLOAD, limit, EXPIRED_PAYLOAD)
        ).rowcount)
        result["mayak_v2.observation_journal.context"] = int(connection.execute(
            compact_journal_sql(), (cutoff, limit)
        ).rowcount)
        for table, trigger in EXPECTED_TRIGGERS.items():
            connection.execute(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}")
        # Required post-write check inside the same transaction.
        check_triggers(connection)
        size = int(connection.execute("SELECT pg_database_size(current_database())").fetchone()[0])
    return Result("APPLIED", cutoff.isoformat(), result, size)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded 24h full-market-telemetry retention")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="read-only candidate counts (default)")
    modes.add_argument("--apply", action="store_true", help="postgres actor only; release gate")
    parser.add_argument("--batch-limit", type=int, default=DEFAULT_BATCH_LIMIT)
    args = parser.parse_args()
    if not 1 <= args.batch_limit <= MAX_BATCH_LIMIT:
        raise SystemExit("RETENTION_BATCH_LIMIT_INVALID")
    import psycopg

    role = "postgres" if args.apply else "cripta"
    dsn = f"dbname=cripta user={role} host=/var/run/postgresql application_name=market-hot-retention"
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as connection:
            cutoff = connection.execute("SELECT clock_timestamp() - interval '24 hours'").fetchone()[0]
            if args.apply:
                done = run_apply(connection, cutoff, args.batch_limit)
            else:
                connection.execute("BEGIN READ ONLY")
                try:
                    done = collect_check(connection, cutoff)
                finally:
                    connection.rollback()
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "error_class": type(exc).__name__,
                          "reason": str(exc)[:180]}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"status": done.mode, "cutoff_utc": done.cutoff_utc,
                      "rows": done.rows, "database_bytes": done.database_bytes},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
