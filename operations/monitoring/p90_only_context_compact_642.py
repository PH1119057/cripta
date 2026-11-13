#!/usr/bin/env python3
"""Issue #642 R12: exact two-table physical bloat reclaim, no row deletion.

Owner: P90L90-only policy; **not** permission to skip backup/restore gates.
Actors: Unix postgres and DB session_user/current_user postgres for --apply;
Unix cripta with SELECT privileges for --check. The reviewed root-owned
installed executable is READ only, local PostgreSQL socket READ/WRITE.
Direct Python filesystem WRITE/CREATE/DELETE/RENAME: NONE.
PostgreSQL owns physical data, index and WAL writes under its own UID.
No arbitrary table, SQL, privilege, index-DROP, service or LIVE interface.
Recovery preflight and independently verified restore stay external release
gates; a successful --check alone never authorizes --apply.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

EXPECTED: dict[str, str] = {
    "dispatcher_v2.coin_market_contexts": "0346935967e32c973832634fbd90baa3",
    "mayak_v2.coin_market_contexts": "5ae861cf6c32dc9f9f05d05f48c6b6cc",
}
REQUIRED_ROWS = 450
REQUIRED_LINK_ROWS = 718
MAX_LOGICAL_PAYLOAD_BYTES = 16 * 1024 * 1024
MIN_ROOT_FREE_BYTES = 700 * 1024 * 1024
MIN_BLOAT_BEFORE_BYTES = 500 * 1024 * 1024
MAX_REWRITTEN_BYTES = 120 * 1024 * 1024


def root_free_bytes() -> int:
    st = os.statvfs("/")
    return int(st.f_bavail * st.f_frsize)


def table_evidence(q: Any, name: str) -> dict[str, Any]:
    if name not in EXPECTED:
        raise RuntimeError("TABLE_NOT_IN_EXACT_COMPACT_MANIFEST")
    q.execute(
        """SELECT count(*),count(DISTINCT coin_context_id),
                  md5(string_agg(
                    coin_context_id||':'||content_hash,'|'
                    ORDER BY coin_context_id
                  )),
                  coalesce(sum(pg_column_size(payload)),0),
                  pg_total_relation_size(%s::regclass),
                  pg_get_userbyid(
                    (SELECT relowner FROM pg_class WHERE oid=%s::regclass)
                  )
           FROM """ + name,
        (name, name),
    )
    rows, distinct_ids, digest, payload_bytes, relation_bytes, owner = q.fetchone()
    if (
        rows != REQUIRED_ROWS
        or distinct_ids != REQUIRED_ROWS
        or digest != EXPECTED[name]
        or payload_bytes > MAX_LOGICAL_PAYLOAD_BYTES
        or owner != "postgres"
    ):
        raise RuntimeError(f"RETAINED_CONTEXT_EVIDENCE_DRIFT:{name}")
    return {
        "rows": int(rows),
        "digest": digest,
        "payload_bytes": int(payload_bytes),
        "relation_bytes": int(relation_bytes),
    }


def verify_links(q: Any) -> None:
    q.execute(
        """SELECT count(*),count(DISTINCT coin_context_id)
           FROM research_context.dispatcher_v2_event_links
           WHERE coin_context_id IS NOT NULL"""
    )
    links, distinct_contexts = q.fetchone()
    if (links, distinct_contexts) != (REQUIRED_LINK_ROWS, REQUIRED_ROWS):
        raise RuntimeError("EVENT_LINK_EVIDENCE_DRIFT")
    q.execute(
        """SELECT count(*)
           FROM dispatcher_v2.coin_market_contexts d
           LEFT JOIN mayak_v2.coin_market_contexts m
             ON m.coin_context_id=d.source_coin_context_id
           WHERE m.coin_context_id IS NULL"""
    )
    if q.fetchone()[0]:
        raise RuntimeError("DISPATCHER_MAYAK_REFERENTIAL_BREAK")


def verify_role(q: Any, *, applying: bool) -> None:
    q.execute("SELECT current_user,session_user,current_database(),pg_is_in_recovery()")
    role, session, database, recovering = q.fetchone()
    if database != "cripta" or recovering:
        raise RuntimeError("WRONG_DATABASE_OR_RECOVERY")
    if applying and (role, session) != ("postgres", "postgres"):
        raise RuntimeError("EXACT_POSTGRES_DB_ROLE_REQUIRED")


def verify_all(q: Any, *, applying: bool) -> dict[str, Any]:
    verify_role(q, applying=applying)
    verify_links(q)
    sizes = {name: table_evidence(q, name) for name in EXPECTED}
    return {"tables": sizes, "root_free_bytes": root_free_bytes()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Exact P90 context bloat reclaim #642")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply:
        import pwd

        if os.geteuid() != pwd.getpwnam("postgres").pw_uid:
            raise SystemExit("EXACT_POSTGRES_UNIX_ACTOR_REQUIRED")

    import psycopg

    role = "postgres" if args.apply else "cripta"
    conn_info = (
        f"dbname=cripta user={role} host=/var/run/postgresql "
        "connect_timeout=5 application_name=p90-context-r12-642"
    )
    completed: list[str] = []
    try:
        with psycopg.connect(conn_info, autocommit=True) as conn:
            with conn.cursor() as q:
                q.execute("SET lock_timeout='3000ms'")
                q.execute("SET statement_timeout='120000ms'")
                before = verify_all(q, applying=args.apply)
                if not args.apply:
                    print(
                        json.dumps({"status": "CHECK", "before": before},
                                   sort_keys=True)
                    )
                    return 0
                if before["root_free_bytes"] < MIN_ROOT_FREE_BYTES:
                    raise RuntimeError("ROOT_REWRITE_RESERVE_TOO_LOW")
                if any(
                    x["relation_bytes"] < MIN_BLOAT_BEFORE_BYTES
                    for x in before["tables"].values()
                ):
                    raise RuntimeError("EXPECTED_BLOAT_ALREADY_GONE")
                q.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s))",
                    ("cripta-p90-context-r12-642",),
                )
                if q.fetchone()[0] is not True:
                    raise RuntimeError("CONTEXT_COMPACT_LOCK_ALREADY_HELD")
                try:
                    q.execute("CHECKPOINT")
                    for name in EXPECTED:
                        if root_free_bytes() < MIN_ROOT_FREE_BYTES:
                            raise RuntimeError("ROOT_RESERVE_LOST_BEFORE_REWRITE")
                        print(
                            json.dumps({"stage": "BEGIN", "table": name},
                                       sort_keys=True),
                            flush=True,
                        )
                        # VACUUM FULL rewrites the table/TOAST and recreates
                        # existing indexes; no rows/index definitions removed.
                        # Autocommit is required; never run inside BEGIN.
                        q.execute("VACUUM (FULL, ANALYZE) " + name)
                        actual = table_evidence(q, name)
                        verify_links(q)
                        if actual["relation_bytes"] > MAX_REWRITTEN_BYTES:
                            raise RuntimeError("COMPACTED_RELATION_UNEXPECTEDLY_LARGE")
                        completed.append(name)
                        q.execute("CHECKPOINT")
                        print(
                            json.dumps(
                                {"stage": "VERIFIED", "table": name,
                                 "after": actual,
                                 "root_free_bytes": root_free_bytes()},
                                sort_keys=True,
                            ),
                            flush=True,
                        )
                    after = verify_all(q, applying=True)
                    print(
                        json.dumps({"status": "DONE", "before": before,
                                    "after": after, "completed": completed},
                                   sort_keys=True),
                        flush=True,
                    )
                    return 0
                finally:
                    q.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))",
                        ("cripta-p90-context-r12-642",),
                    )
    except Exception as error:
        print(
            json.dumps(
                {"status": "BLOCKED_OR_PARTIAL",
                 "completed": completed,
                 "error_class": type(error).__name__,
                 "reason": str(error)[:260]},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
