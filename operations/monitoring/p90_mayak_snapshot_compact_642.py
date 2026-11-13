#!/usr/bin/env python3
"""Issue #642: physical-only, exactly one MAYAK snapshot VACUUM FULL.

No row DELETE, TRUNCATE, index DROP, FK mutation, filesystem write or
LIVE/Exchange interaction. All 7,883 rows and 450 FK-linked IDs preserved.
Unix+DB postgres for apply. A separate confirmed restore+SHA-256 proof is
required. One exact allowlisted command, no arbitrary SQL argument.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

PROOF_FILE = Path("/usr/local/libexec/cripta-p90-mayak-snapshot-proof-642.json")
TABLE = "mayak_v2.snapshots"
ROWS = 7883
INDEXES = 3
PINNED = 450
FK_COUNT = 9
MIN_ROOT_FREE = 2 * 1024**3
MIN_POST_FREE = 512 * 1024**2
MAX_WAL_BEFORE = 768 * 1024**2
MIN_RELATION_BEFORE = 900 * 1024**2
LOCK_KEY = "cripta-mayak-snapshot-physical-r12-642"
REFERENCES = (
    ("dispatcher_v2.coin_market_contexts", "source_mayak_snapshot_id", 450),
    ("dispatcher_v2.global_market_contexts", "source_mayak_snapshot_id", 450),
    ("mayak_v2.coin_market_contexts", "mayak_snapshot_id", 450),
    ("mayak_v2.shared_market_contexts", "mayak_snapshot_id", 450),
    ("mayak_v2.coin_minutes", "snapshot_id", 0),
    ("mayak_v2.events", "snapshot_id", 0),
    ("mayak_v2.observation_journal", "snapshot_id", 0),
    ("mayak_v2.state_events", "snapshot_id", 0),
    ("research_context.event_links", "observed_mayak_snapshot_id", 0),
)
ROW_COPY = (
    "COPY (SELECT row_to_json(t)::text FROM mayak_v2.snapshots t "
    "ORDER BY t.id) TO STDOUT WITH (FORMAT text)"
)


def disk_free() -> int:
    v = os.statvfs("/")
    return int(v.f_bavail * v.f_frsize)


def proof() -> dict[str, Any]:
    fd = os.open(PROOF_FILE, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if (
            not stat.S_ISREG(st.st_mode) or st.st_uid != 0
            or stat.S_IMODE(st.st_mode) != 0o644 or st.st_size > 131072
        ):
            raise RuntimeError("UNTRUSTED_ROOT_OWNED_PROOF_MANIFEST")
        document = json.loads(os.read(fd, st.st_size))
    finally:
        os.close(fd)
    if (
        document.get("status") != "SCOPED_MAYAK_SNAPSHOT_RESTORE_PASS"
        or document.get("source_table") != TABLE
        or document.get("full_production_cluster_restore") is not False
        or document.get("source_evidence") != document.get("restored_evidence")
    ):
        raise RuntimeError("INDEPENDENT_MAYAK_RESTORE_NOT_PROVEN")
    e = document["source_evidence"]
    refs = document["references"]
    if (
        e["rows"] != ROWS or e["indexes"] != INDEXES
        or len(e["content_sha256"]) != 64
        or len(document.get("archive_sha256", "")) != 64
        or not 4096 < document.get("archive_bytes", 0) < 1024**3
        or len(refs.get("pinned_ids", [])) != PINNED
        or len(set(refs["pinned_ids"])) != PINNED
        or len(refs.get("by_fk", {})) != FK_COUNT
    ):
        raise RuntimeError("RECOVERED_MAYAK_PROOF_SCOPE_DRIFT")
    return document


def reference_truth(q: Any) -> dict[str, Any]:
    union: set[int] = set()
    by_fk: dict[str, Any] = {}
    q.execute(
        "SELECT count(*) FROM pg_constraint WHERE contype='f' "
        "AND confrelid=%s::regclass", (TABLE,),
    )
    if q.fetchone()[0] != FK_COUNT:
        raise RuntimeError("CURRENT_INBOUND_FK_COUNT_DRIFT")
    for name, col, expected in REFERENCES:
        q.execute(
            "SELECT " + col + " FROM " + name
            + " WHERE " + col + " IS NOT NULL ORDER BY " + col
        )
        ids = [int(a[0]) for a in q.fetchall()]
        if len(ids) != expected or len(set(ids)) != expected:
            raise RuntimeError("CURRENT_REFERENCE_SCOPE_DRIFT:" + name)
        digest = hashlib.sha256()
        for value in ids:
            digest.update(str(value).encode("ascii") + b"\n")
        by_fk[name + "." + col] = {
            "count": len(ids), "sha256": digest.hexdigest(),
        }
        union.update(ids)
    if len(union) != PINNED:
        raise RuntimeError("CURRENT_PINNED_ID_UNION_DRIFT")
    q.execute(
        "SELECT count(*) FROM " + TABLE + " WHERE id=ANY(%s)",
        (sorted(union),),
    )
    if q.fetchone()[0] != PINNED:
        raise RuntimeError("CURRENT_450_PINNED_SNAPSHOT_ROWS_MISSING")
    return {"pinned_ids": sorted(union), "by_fk": by_fk}


def evidence(q: Any) -> dict[str, Any]:
    q.execute("SELECT current_database(),pg_is_in_recovery()")
    if q.fetchone() != ("cripta", False):
        raise RuntimeError("WRONG_PRODUCTION_DATABASE")
    q.execute(
        "SELECT relkind,pg_get_userbyid(relowner),pg_total_relation_size(oid) "
        "FROM pg_class WHERE oid=%s::regclass", (TABLE,),
    )
    rel = q.fetchone()
    if rel is None or rel[:2] != ("r", "cripta"):
        raise RuntimeError("MAYAK_RELATION_OWNER_DRIFT")
    q.execute("SELECT count(*),count(DISTINCT id) FROM " + TABLE)
    if q.fetchone() != (ROWS, ROWS):
        raise RuntimeError("SNAPSHOT_SOURCE_ROWS_DRIFT")
    q.execute(
        "SELECT count(*),bool_and(indisvalid AND indisready) "
        "FROM pg_index WHERE indrelid=%s::regclass", (TABLE,),
    )
    if q.fetchone() != (INDEXES, True):
        raise RuntimeError("THREE_SNAPSHOT_INDEXES_DRIFT")
    q.execute(
        "SELECT count(*) FROM pg_constraint WHERE contype='p' "
        "AND conrelid=%s::regclass", (TABLE,),
    )
    if q.fetchone()[0] != 1:
        raise RuntimeError("SNAPSHOT_PRIMARY_KEY_DRIFT")
    q.execute("SET TIME ZONE 'UTC'")
    q.execute("SET extra_float_digits=3")
    hash_value = hashlib.sha256()
    total = 0
    with q.copy(ROW_COPY) as stream:
        for chunk in stream:
            hash_value.update(chunk)
            total += len(chunk)
    return {
        "rows": ROWS, "indexes": INDEXES,
        "content_sha256": hash_value.hexdigest(),
        "serialized_bytes": total, "relation_bytes": int(rel[2]),
        "root_free_bytes": disk_free(),
    }


def wal_size(q: Any) -> int:
    q.execute("SELECT coalesce(sum(size),0) FROM pg_ls_waldir()")
    return int(q.fetchone()[0])


def execute() -> int:
    import pwd

    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--apply"):
        raise RuntimeError("ONLY_FIXED_CHECK_AND_APPLY_MODES")
    applying = sys.argv[1] == "--apply"
    if applying and os.geteuid() != pwd.getpwnam("postgres").pw_uid:
        raise RuntimeError("UNIX_POSTGRES_ACTOR_REQUIRED")
    if not applying and os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise RuntimeError("UNIX_CRIPTA_READ_ACTOR_REQUIRED")
    import psycopg

    manifest = proof()
    role = "postgres" if applying else "cripta"
    with psycopg.connect(
        dbname="cripta", user=role, host="/var/run/postgresql",
        connect_timeout=5, autocommit=True,
        application_name="p90-mayak-snapshot-compact-642",
    ) as conn:
        with conn.cursor() as q:
            q.execute("SELECT current_user,session_user")
            if q.fetchone() != (role, role):
                raise RuntimeError("EFFECTIVE_DB_ROLE_MISMATCH")
            q.execute("SET lock_timeout='3000ms'")
            q.execute("SET statement_timeout='240000ms'")
            before = evidence(q)
            links_before = reference_truth(q)
            expected = manifest["source_evidence"]
            if (
                before["content_sha256"] != expected["content_sha256"]
                or before["serialized_bytes"] != expected["serialized_bytes"]
                or links_before != manifest["references"]
            ):
                raise RuntimeError("ACTUAL_ROWS_OR_450_FK_LINKS_DIFFER_FROM_RESTORE")
            if not applying:
                print(json.dumps({
                    "status": "CHECK", "before": before,
                    "pinned": len(links_before["pinned_ids"]),
                }, sort_keys=True), flush=True)
                return 0
            if before["root_free_bytes"] < MIN_ROOT_FREE:
                raise RuntimeError("ROOT_CAPACITY_INSUFFICIENT")
            if before["relation_bytes"] < MIN_RELATION_BEFORE:
                raise RuntimeError("RELATION_ALREADY_COMPACTED_OR_SCOPE_DRIFT")
            wal_before = wal_size(q)
            if wal_before > MAX_WAL_BEFORE:
                raise RuntimeError("WAL_BEFORE_TOO_LARGE")
            q.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                "AND state='active' AND query ILIKE '%mayak_v2.snapshots%'"
            )
            if q.fetchone()[0]:
                raise RuntimeError("CONFLICTING_ACTIVE_SNAPSHOT_QUERY")
            q.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (LOCK_KEY,))
            if q.fetchone()[0] is not True:
                raise RuntimeError("MAYAK_SNAPSHOT_R12_LOCK_CONFLICT")
            try:
                q.execute("CHECKPOINT")
                if disk_free() < MIN_ROOT_FREE:
                    raise RuntimeError("ROOT_CAPACITY_CHANGED_BEFORE_REWRITE")
                print(json.dumps({
                    "stage": "VACUUM_FULL_STARTED", "before": before,
                    "wal_before_bytes": wal_before,
                }, sort_keys=True), flush=True)
                q.execute("VACUUM (FULL, ANALYZE) mayak_v2.snapshots")
                after = evidence(q)
                links_after = reference_truth(q)
                if (
                    after["content_sha256"] != expected["content_sha256"]
                    or after["serialized_bytes"] != expected["serialized_bytes"]
                    or links_after != links_before
                ):
                    raise RuntimeError("POST_REWRITE_ROWS_OR_FK_LINKS_DIFFER")
                if after["root_free_bytes"] < MIN_POST_FREE:
                    raise RuntimeError("POST_REWRITE_ROOT_CAPACITY_LOW")
                q.execute("CHECKPOINT")
                print(json.dumps({
                    "status": "DONE", "before": before, "after": after,
                    "relation_reclaim_bytes": (
                        before["relation_bytes"] - after["relation_bytes"]
                    ), "wal_after_bytes": wal_size(q),
                }, sort_keys=True), flush=True)
                return 0
            finally:
                q.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,))


if __name__ == "__main__":
    try:
        raise SystemExit(execute())
    except Exception as exc:
        print(json.dumps({
            "status": "MAYAK_R12_FAILED_OR_UNKNOWN",
            "error_class": type(exc).__name__,
            "reason": str(exc)[:360],
        }, sort_keys=True), file=sys.stderr)
        raise SystemExit(2) from exc
