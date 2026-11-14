#!/usr/bin/env python3
"""Issue #642: one-time retirement of September V1 shadow parity evidence.

Owner-approved disposable advisory class (ARCH §61.2 and §61.2.1).
Only these exact two tables; no CASCADE, no other DELETE, no new backup,
no filesystem writes, no LIVE/Exchange action, no service restart.

--check: any SELECT-capable Unix/DB role; READ ONLY.
--apply: exact root-owned installed script, Unix AND DB postgres migration role.
Both modes fail closed on FK, trigger, table, actor, or row-scope drift.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
from typing import Any

EVENTS = "strategy_entry.shadow_parity_events"
RUNS = "strategy_entry.shadow_parity_runs"
EXPECTED_EVENTS = 6854
EXPECTED_RUNS = 620
RETIRED_STRATEGY = "entry_v1_core"
RETIRED_VERSION = "1.0-live-first-touch"
RETIRED_STATUS = "NOT_COMPARABLE"
EARLIEST = "2026-09-08T00:00:00+00:00"
LATEST = "2026-09-13T00:00:00+00:00"
FK_NAME = "shadow_parity_events_parity_run_id_fkey"
TRIGGERS = (
    (EVENTS, "shadow_parity_events_immutable"),
    (RUNS, "shadow_parity_runs_delete_immutable"),
    (RUNS, "shadow_parity_runs_guard_update"),
)


def require(value: bool, status: str) -> None:
    if not value:
        raise RuntimeError("RETIRED_PARITY_642_BLOCKED:" + status)


def evidence(cursor: Any, *, empty: bool) -> dict[str, Any]:
    cursor.execute(
        """SELECT current_user,session_user,current_database(),
                  pg_is_in_recovery()"""
    )
    actor, session, database, recovery = cursor.fetchone()
    require(database == "cripta" and not recovery, "DATABASE_IDENTITY")
    cursor.execute(
        """SELECT conrelid::regclass::text,confrelid::regclass::text,conname
             FROM pg_constraint
            WHERE contype='f'
              AND confrelid=ANY(ARRAY[%s::regclass,%s::regclass])
            ORDER BY 1,2,3""",
        (EVENTS, RUNS),
    )
    require(
        cursor.fetchall() == [(EVENTS, RUNS, FK_NAME)],
        "EXTERNAL_FK_REFERENCE_DRIFT",
    )
    cursor.execute(
        """SELECT t.tgrelid::regclass::text,t.tgname,t.tgenabled
             FROM pg_trigger t
            WHERE t.tgrelid=ANY(ARRAY[%s::regclass,%s::regclass])
              AND NOT t.tgisinternal
            ORDER BY 1,2""",
        (EVENTS, RUNS),
    )
    current_triggers = cursor.fetchall()
    require(
        current_triggers
        == sorted((table, trigger, "O") for table, trigger in TRIGGERS),
        "IMMUTABLE_TRIGGER_DRIFT",
    )
    cursor.execute(
        """SELECT c.oid::regclass::text,pg_get_userbyid(c.relowner),
                  pg_total_relation_size(c.oid)
             FROM pg_class c
            WHERE c.oid=ANY(ARRAY[%s::regclass,%s::regclass])
            ORDER BY 1""",
        (EVENTS, RUNS),
    )
    relation = cursor.fetchall()
    require(
        len(relation) == 2
        and all(owner == "postgres" for _, owner, _ in relation),
        "TABLE_OWNERSHIP_DRIFT",
    )
    cursor.execute(
        """SELECT count(*),count(DISTINCT parity_run_id),
                  count(*) FILTER(WHERE source_refs <> '[]'::jsonb),
                  min(event_at),max(event_at)
             FROM strategy_entry.shadow_parity_events"""
    )
    events, distinct_runs, nonempty_source_refs, first_event, last_event = (
        cursor.fetchone()
    )
    cursor.execute(
        """SELECT count(*),
                  count(*) FILTER(
                    WHERE strategy_id=%s AND strategy_version=%s
                      AND status=%s),
                  min(started_at),max(started_at)
             FROM strategy_entry.shadow_parity_runs""",
        (RETIRED_STRATEGY, RETIRED_VERSION, RETIRED_STATUS),
    )
    runs, qualified, first_run, last_run = cursor.fetchone()
    if empty:
        require(
            events == runs == distinct_runs == qualified == 0,
            "POST_TRUNCATE_NONEMPTY",
        )
    else:
        from datetime import datetime

        earliest = datetime.fromisoformat(EARLIEST)
        latest = datetime.fromisoformat(LATEST)
        require(
            events == EXPECTED_EVENTS
            and runs == distinct_runs == qualified == EXPECTED_RUNS
            and nonempty_source_refs == 0
            and all(
                timestamp is not None
                and earliest <= timestamp < latest
                for timestamp in (
                    first_event, last_event, first_run, last_run
                )
            ),
            "RETIRED_V1_ROW_SCOPE_DRIFT",
        )
    return {
        "unix_expected": "postgres" if empty else "cripta_or_postgres",
        "db_user": actor,
        "db_session": session,
        "event_rows": events,
        "parity_runs": runs,
        "retained_table_bytes": {
            table: int(size) for table, _, size in relation
        },
        "foreign_keys": 1,
        "immutable_triggers_enabled": len(current_triggers),
        "scope": "RETIRED_ENTRY_V1_NONCOMPARABLE_20260908_20260912",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--check", action="store_true")
    actions.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    require(os.name == "posix", "LINUX_REQUIRED")
    import psycopg

    username = pwd.getpwuid(os.geteuid()).pw_name
    if args.apply:
        require(username == "postgres", "UNIX_MIGRATION_ACTOR")
    elif username not in {"cripta", "postgres"}:
        raise RuntimeError("RETIRED_PARITY_642_BLOCKED:READ_ACTOR")

    with psycopg.connect(
        "dbname=cripta user=" + username + " host=/var/run/postgresql",
        autocommit=True,
        options="-c statement_timeout=25000 -c lock_timeout=2000",
    ) as connection:
        if args.check:
            with connection.transaction():
                connection.execute("SET LOCAL TRANSACTION READ ONLY")
                before = evidence(connection.cursor(), empty=False)
            print(json.dumps({
                "status": "READ_ONLY_READY_NO_MUTATION",
                "evidence": before,
            }, sort_keys=True))
            return 0

        with connection.transaction():
            connection.execute("SET LOCAL lock_timeout='2000ms'")
            connection.execute("SET LOCAL statement_timeout='25000ms'")
            # Never wait behind an unknown writer. Serial lock includes child.
            connection.execute(
                "LOCK TABLE strategy_entry.shadow_parity_events, "
                "strategy_entry.shadow_parity_runs "
                "IN ACCESS EXCLUSIVE MODE NOWAIT"
            )
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("CRIPTA_642_RETIRED_PARITY_R11",),
            )
            before = evidence(connection.cursor(), empty=False)
            require(
                (before["db_user"], before["db_session"])
                == ("postgres", "postgres"),
                "DB_MIGRATION_ACTOR",
            )
            # Owner-approved two-table retirement. PostgreSQL TRUNCATE is
            # transactional, requires both listed tables due to their FK,
            # and preserves the schemas, constraints and immutable triggers.
            connection.execute(
                "TRUNCATE TABLE strategy_entry.shadow_parity_events, "
                "strategy_entry.shadow_parity_runs"
            )
            after = evidence(connection.cursor(), empty=True)
        print(json.dumps({
            "status": "APPLIED_COMMITTED",
            "removed_events": before["event_rows"],
            "removed_runs": before["parity_runs"],
            "before": before,
            "after": after,
        }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
