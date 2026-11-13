#!/usr/bin/env python3
"""One-shot exact six cold-table HOT retirement, Issue #642.

Authority: GitHub main ARCH §61, owner-approved eight packages.
BEFORE: six *_cold_20261107 copies remain in production PostgreSQL.
AFTER: table schemas/column identities remain but all six tables contain
zero rows; original live P90L90, accounts, native events, Execution and
Exit/MFE data are not touched.

Filesystem actor/path contract:
* Unix writer: postgres; DB writer: session_user=current_user=postgres.
* Filesystem READ: this installed script, local PG Unix socket.
* Filesystem WRITE/CREATE/RENAME/DELETE: NONE by this script.
  PostgreSQL performs its own relation/WAL writes as postgres.
* Current code deployment is separately Git-first, owner-approved,
  with immutable SHA verification; SentinelX authorizes exactly this
  script path and --apply, not arbitrary SQL or shell.
* Transaction rollback on SQL failure; system backup remains present.
* No LIVE arming, service restart, trigger disabling or CASCADE.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import sys

TABLE_ROWS: dict[str, int] = {
    "dispatcher_v2.global_market_contexts_cold_20261107": 26672,
    "mayak_v2.coin_minutes_cold_20261107": 2480,
    "mayak_v2.observation_journal_cold_20261107": 26672,
    "mayak_v2.shared_market_contexts_cold_20261107": 26672,
    "mayak_v2.state_events_cold_20261107": 13020,
    "research_context.dispatcher_v2_event_links_cold_20261107": 1899,
}

EXPECTED_TOTAL_ROWS = 97415
MIN_FREE_BYTES = 32 * 1024 * 1024


def truncate_sql() -> str:
    """Exact static DDL; intentionally never exposes a generic table argument."""
    assert sum(TABLE_ROWS.values()) == EXPECTED_TOTAL_ROWS
    return "TRUNCATE TABLE " + ", ".join(TABLE_ROWS)


def _preflight(cursor: object, apply: bool) -> dict[str, object]:
    cursor.execute("SELECT current_user,session_user,current_database(),pg_is_in_recovery()")
    role, session, db, recovery = cursor.fetchone()
    if db != "cripta" or recovery:
        raise RuntimeError("WRONG_DB_OR_IN_RECOVERY")
    if apply:
        if os.geteuid() != pwd.getpwnam("postgres").pw_uid or (role, session) != ("postgres", "postgres"):
            raise RuntimeError("POSTGRES_UNIX_AND_DB_ACTOR_REQUIRED")
        vfs = os.statvfs("/")
        if vfs.f_bavail * vfs.f_frsize < MIN_FREE_BYTES:
            raise RuntimeError("SYSTEM_SSD_TOO_FULL_FOR_POSTGRES_TRUNCATE")

    names = list(TABLE_ROWS)
    cursor.execute("""
        SELECT c.oid::regclass::text, c.relkind, pg_get_userbyid(c.relowner),
               pg_total_relation_size(c.oid),
               (SELECT count(*) FROM pg_constraint f
                WHERE f.contype='f' AND (f.conrelid=c.oid OR f.confrelid=c.oid)),
               (SELECT count(*) FROM pg_trigger t
                WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
        FROM pg_class c
        WHERE c.oid::regclass::text=ANY(%s)
        ORDER BY c.oid::regclass::text
    """, (names,))
    rows = cursor.fetchall()
    if len(rows) != len(names):
        raise RuntimeError("COLD_TABLE_MISSING")
    sizes: dict[str, int] = {}
    for name, kind, owner, size, fks, triggers in rows:
        if name not in TABLE_ROWS or kind != "r" or owner != "postgres" or fks or triggers:
            raise RuntimeError(f"COLD_TABLE_CONTRACT_CHANGED:{name}")
        sizes[name] = int(size)
    counts: dict[str, int] = {}
    for table, expected in TABLE_ROWS.items():
        cursor.execute(f"SELECT count(*) FROM {table}")
        actual = int(cursor.fetchone()[0])
        if actual != expected:
            raise RuntimeError(f"COLD_TABLE_ROW_COUNT_DRIFT:{table}:{actual}:{expected}")
        counts[table] = actual
    cursor.execute("SELECT pg_database_size(current_database())")
    return {"rows": counts, "relation_bytes": sizes,
            "database_bytes": int(cursor.fetchone()[0])}


def main() -> int:
    parser = argparse.ArgumentParser(description="P90-only approved cold SQL table retirement #642")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    opts = parser.parse_args()

    # Even an accidental --apply from a non-postgres Unix actor is rejected.
    if opts.apply and os.geteuid() != pwd.getpwnam("postgres").pw_uid:
        raise SystemExit("POSTGRES_UNIX_ACTOR_REQUIRED")
    import psycopg

    role = "postgres" if opts.apply else "cripta"
    dsn = f"dbname=cripta user={role} host=/var/run/postgresql connect_timeout=5 application_name=p90-only-cold-642"
    conn = psycopg.connect(dsn)
    committed = False
    before: dict[str, object] = {}
    try:
        with conn.cursor() as q:
            q.execute("SET LOCAL lock_timeout = '3000ms'")
            q.execute("SET LOCAL statement_timeout = '25000ms'")
            if opts.apply:
                q.execute("LOCK TABLE " + ", ".join(TABLE_ROWS) + " IN ACCESS EXCLUSIVE MODE NOWAIT")
            before = _preflight(q, opts.apply)
            if opts.apply:
                q.execute(truncate_sql())
                for table in TABLE_ROWS:
                    q.execute(f"SELECT count(*) FROM {table}")
                    if q.fetchone()[0] != 0:
                        raise RuntimeError(f"POST_TRUNCATE_NONZERO:{table}")
                conn.commit()
                committed = True
            else:
                conn.rollback()
        print(json.dumps({"status": "APPLIED" if committed else "CHECK",
                          "tables": len(TABLE_ROWS),
                          "rows_targeted": EXPECTED_TOTAL_ROWS,
                          "before": before}, sort_keys=True))
        return 0
    except Exception as exc:
        if not committed:
            try:
                conn.rollback()
            except Exception:
                pass
        print(json.dumps({"status": "BLOCKED_OR_UNKNOWN",
                          "error": type(exc).__name__,
                          "reason": str(exc)[:300]}, sort_keys=True), file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
