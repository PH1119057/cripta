#!/usr/bin/env python3
"""Bounded Issue #642 purge of obsolete non-P90 minute-market observations.

BEFORE: analytics.market_minutes contains 20 symbols, including 13 retired
MAYAK/Dispatcher-only symbols. AFTER: remove only rows of those 13 retired
symbols earlier than the exact 2026-11-12 UTC fence. Keep original schema,
indexes, all P90 symbols and BTC/ETH reference histories untouched.

Actor/path contract (DEV §5.1, §19):
SCRIPT: this exact GitHub-reviewed source, installed immutable under
/usr/local/libexec/cripta-p90-only-market-minutes-642.py by approved release.
DB actor and Unix actor for --apply: postgres. --check: any SELECT role.
READ: this installed file and /var/run/postgresql local socket.
WRITE/CREATE/RENAME/DELETE by Python on filesystem: NONE.
PostgreSQL writes its own WAL/relations using postgres account.
No trigger changes, CASCADE, DROP, VACUUM FULL, REINDEX, or LIVE actions.
Batched commits + PostgreSQL CHECKPOINT permit safe WAL recycling; automatic
stop on insufficient actual root filesystem free space. No autonomous restart.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

TABLE = "analytics.market_minutes"
SOURCE = "ms-bybit-mainnet-public-v1"
LEGACY_SYMBOLS = (
    "AAVEUSDT", "ADAUSDT", "AVAXUSDT", "BCHUSDT", "BNBUSDT",
    "HBARUSDT", "LINKUSDT", "OPUSDT", "SOLUSDT", "SUIUSDT",
    "TRXUSDT", "UNIUSDT", "XRPUSDT",
)
PROTECTED_SYMBOLS = (
    "LTCUSDT", "INJUSDT", "DOTUSDT", "ARBUSDT", "APTUSDT",
    "BTCUSDT", "ETHUSDT",
)
CUTOFF = datetime(2026, 11, 12, tzinfo=timezone.utc)
EXPECTED_LEGACY_PER_SYMBOL = 65180
BATCH = 2500
MAX_ROWS_PER_INVOCATION = 25000
MIN_ROOT_FREE_BYTES = 320 * 1024 * 1024
LOCK_KEY = "cripta-p90-only-minute-retention-642"


def root_free_bytes() -> int:
    fs = os.statvfs("/")
    return int(fs.f_bavail * fs.f_frsize)


def rows_to_delete_sql() -> str:
    """Static exact relation; only row-filter parameters enter the query."""
    return (
        "WITH chosen AS (SELECT ctid FROM analytics.market_minutes "
        "WHERE external_symbol = ANY(%s::text[]) AND observed_minute < %s "
        "LIMIT %s FOR UPDATE SKIP LOCKED) "
        "DELETE FROM analytics.market_minutes target USING chosen "
        "WHERE target.ctid=chosen.ctid"
    )


def verify_scope(q: Any, *, applying: bool) -> dict[str, Any]:
    q.execute("SELECT current_user,session_user,current_database(),pg_is_in_recovery()")
    actor, session, database, recovery = q.fetchone()
    if database != "cripta" or recovery:
        raise RuntimeError("DATABASE_OR_RECOVERY_DRIFT")
    if applying and (actor, session) != ("postgres", "postgres"):
        raise RuntimeError("POSTGRES_DB_ACTOR_REQUIRED")

    q.execute(
        """SELECT relkind,pg_get_userbyid(relowner),
          (SELECT count(*) FROM pg_constraint f
           WHERE f.contype='f' AND f.confrelid=c.oid),
          (SELECT count(*) FROM pg_trigger t
           WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
          pg_total_relation_size(c.oid)
        FROM pg_class c WHERE c.oid=%s::regclass""",
        (TABLE,),
    )
    found = q.fetchone()
    if found is None or found[:4] != ("r", "postgres", 0, 0):
        raise RuntimeError(f"TABLE_SCHEMA_OR_CONSUMER_DRIFT:{found!r}")

    q.execute(
        """SELECT external_symbol,market_source_id,count(*),
                  min(observed_minute),max(observed_minute)
           FROM analytics.market_minutes GROUP BY 1,2""",
    )
    entries = q.fetchall()
    allowed = set(LEGACY_SYMBOLS) | set(PROTECTED_SYMBOLS)
    seen: set[str] = set()
    legacy_count = 0
    protected_count = 0
    for symbol, source, count, _first, last in entries:
        if source != SOURCE or symbol not in allowed or symbol in seen:
            raise RuntimeError(f"MARKET_IDENTITY_DRIFT:{symbol}:{source}")
        seen.add(symbol)
        if symbol in LEGACY_SYMBOLS:
            if not 0 <= count <= EXPECTED_LEGACY_PER_SYMBOL:
                raise RuntimeError(f"LEGACY_COUNT_DRIFT:{symbol}:{count}")
            if count and last >= CUTOFF:
                raise RuntimeError(f"LEGACY_AFTER_CUTOFF:{symbol}:{last}")
            legacy_count += count
        else:
            if count < 60000:
                raise RuntimeError(f"PROTECTED_HISTORY_INCOMPLETE:{symbol}:{count}")
            protected_count += count
    if set(PROTECTED_SYMBOLS) - seen:
        raise RuntimeError("PROTECTED_SYMBOL_MISSING")
    if not set(LEGACY_SYMBOLS).issuperset(seen - set(PROTECTED_SYMBOLS)):
        raise RuntimeError("UNKNOWN_LEGACY_SYMBOL")
    return {
        "legacy_rows": legacy_count,
        "protected_rows": protected_count,
        "size_bytes": int(found[4]),
        "root_free_bytes": root_free_bytes(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Owner-approved P90-only minute retention #642"
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        import pwd
        if os.geteuid() != pwd.getpwnam("postgres").pw_uid:
            raise SystemExit("POSTGRES_UNIX_ACTOR_REQUIRED")
    import psycopg

    role = "postgres" if args.apply else "cripta"
    dsn = (
        f"dbname=cripta user={role} host=/var/run/postgresql "
        "connect_timeout=5 application_name=p90-only-minute-retention-642"
    )
    deleted_total = 0
    status = "CHECK"
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            with conn.cursor() as q:
                q.execute("SET lock_timeout='2000ms'")
                q.execute("SET statement_timeout='30000ms'")
                before = verify_scope(q, applying=args.apply)
                if args.check:
                    print(
                        json.dumps({"status": status, "scope": before}, sort_keys=True)
                    )
                    return 0
                if before["root_free_bytes"] < MIN_ROOT_FREE_BYTES:
                    raise RuntimeError("LOW_ROOT_RESERVE_BEFORE_MUTATION")
                locked = q.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s))", (LOCK_KEY,),
                ).fetchone()[0]
                if not locked:
                    raise RuntimeError("PURGE_LOCK_HELD")
                try:
                    # Important: CHECKPOINT is legal as postgres on an
                    # autocommit connection. Never manipulate pg_wal files.
                    q.execute("CHECKPOINT")
                    remaining_budget = min(
                        MAX_ROWS_PER_INVOCATION, before["legacy_rows"]
                    )
                    while remaining_budget:
                        if root_free_bytes() < MIN_ROOT_FREE_BYTES:
                            status = "PARTIAL_ROOT_RESERVE_STOP"
                            break
                        limit = min(BATCH, remaining_budget)
                        with conn.transaction():
                            q.execute("SET LOCAL lock_timeout='2000ms'")
                            q.execute("SET LOCAL statement_timeout='30000ms'")
                            q.execute(
                                rows_to_delete_sql(),
                                (list(LEGACY_SYMBOLS), CUTOFF, limit),
                            )
                            removed = q.rowcount
                            if not 0 <= removed <= limit:
                                raise RuntimeError("DELETE_ROWCOUNT_DRIFT")
                        deleted_total += removed
                        remaining_budget -= removed
                        q.execute("CHECKPOINT")
                        print(
                            json.dumps(
                                {"stage": "BATCH_COMMITTED", "deleted": deleted_total,
                                 "root_free_bytes": root_free_bytes()},
                                sort_keys=True,
                            ),
                            flush=True,
                        )
                        if removed == 0:
                            break
                    after = verify_scope(q, applying=True)
                    if after["protected_rows"] != before["protected_rows"]:
                        raise RuntimeError("PROTECTED_HISTORY_CHANGED")
                    if after["legacy_rows"] != before["legacy_rows"] - deleted_total:
                        raise RuntimeError("LEGACY_ROWCOUNT_DRIFT_AFTER_COMMIT")
                    if status == "CHECK":
                        status = "DONE" if after["legacy_rows"] == 0 else "PARTIAL"
                    print(
                        json.dumps(
                            {"status": status, "deleted_committed": deleted_total,
                             "before": before, "after": after},
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    return 0
                finally:
                    q.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,))
    except Exception as exc:
        print(
            json.dumps(
                {"status": "BLOCKED_OR_PARTIAL_UNKNOWN",
                 "deleted_committed_reported": deleted_total,
                 "error_class": type(exc).__name__, "reason": str(exc)[:240]},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
