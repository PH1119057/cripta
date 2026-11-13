#!/usr/bin/env python3
"""Issue #642: exact physical-only PostgreSQL market-minute R12 compaction.

Approved scope: analytics.market_minutes, 483770 current rows, seven cohorts.
Production actor: Unix AND PostgreSQL postgres. PostgreSQL-owned physical
relation/WAL writes only. Python filesystem writes/deletes: NONE. Proof:
read-only root-owned /usr/local/libexec/cripta-p90-market-minutes-proof-642.json,
independently restored on Data disk and separately verified before install.
No arbitrary SQL parameters, DROP, row DELETE, index DROP or service restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

TABLE = "analytics.market_minutes"
PROOF_FILE = Path("/usr/local/libexec/cripta-p90-market-minutes-proof-642.json")
SOURCE_ID = "ms-bybit-mainnet-public-v1"
PROTECTED = (
    "APTUSDT", "ARBUSDT", "BTCUSDT", "DOTUSDT", "ETHUSDT",
    "INJUSDT", "LTCUSDT",
)
ROWS_PER_SYMBOL = 69110
ROWS = 483770
INDEXES = 3
MIN_FREE_BEFORE_BYTES = 5 * 512 * 1024 * 1024
MIN_FREE_AFTER_BYTES = 512 * 1024 * 1024
MAX_WAL_BEFORE_BYTES = 768 * 1024 * 1024
MIN_RELATION_BEFORE_BYTES = 1600 * 1024 * 1024
LOCK_KEY = "cripta-market-minutes-r12-642"
ROW_HASH_QUERY = (
    "COPY (SELECT row_to_json(t)::text FROM analytics.market_minutes t "
    "ORDER BY t.market_source_id, t.instrument_id, t.observed_minute) "
    "TO STDOUT WITH (FORMAT text)"
)


def root_free() -> int:
    fs = os.statvfs("/")
    return int(fs.f_bavail * fs.f_frsize)


def proof() -> dict[str, Any]:
    fd = os.open(PROOF_FILE, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if (
            not stat.S_ISREG(s.st_mode)
            or s.st_uid != 0
            or stat.S_IMODE(s.st_mode) != 0o644
            or s.st_size > 16384
        ):
            raise RuntimeError("UNTRUSTED_RECOVERY_MANIFEST")
        data = json.loads(os.read(fd, s.st_size))
    finally:
        os.close(fd)
    if (
        data.get("status") != "SCOPED_MARKET_MINUTES_RESTORE_PASS"
        or data.get("full_production_cluster_restore") is not False
        or data.get("source_table") != TABLE
    ):
        raise RuntimeError("RECOVERY_PROOF_STATUS_DRIFT")
    before = data["source_evidence"]
    after = data["restored_evidence"]
    common = ("rows", "unique_pk", "groups", "content_sha256",
              "serialized_bytes")
    if any(before.get(k) != after.get(k) for k in common):
        raise RuntimeError("INDEPENDENT_RESTORE_FULL_ROW_MISMATCH")
    if (
        before["rows"] != ROWS
        or before["unique_pk"] != ROWS
        or before["groups"]
        != [[name, SOURCE_ID, ROWS_PER_SYMBOL] for name in PROTECTED]
        or len(before["content_sha256"]) != 64
        or len(data.get("archive_sha256", "")) != 64
        or data.get("archive_bytes", 0) < 4096
    ):
        raise RuntimeError("RECOVERY_PROOF_SCOPE_INVALID")
    return data


def gate(q: Any, *, applying: bool) -> dict[str, Any]:
    q.execute("SELECT current_user,session_user,current_database(),pg_is_in_recovery()")
    actor, session, database, recovering = q.fetchone()
    if database != "cripta" or recovering:
        raise RuntimeError("DATABASE_OR_RECOVERY_INVALID")
    if applying and (actor, session) != ("postgres", "postgres"):
        raise RuntimeError("POSTGRES_DATABASE_ACTOR_REQUIRED")
    q.execute(
        """SELECT relkind,pg_get_userbyid(relowner),relrowsecurity,
                  (SELECT count(*) FROM pg_constraint f
                   WHERE f.contype='f' AND f.confrelid=c.oid),
                  (SELECT count(*) FROM pg_trigger t
                   WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
                  pg_total_relation_size(c.oid)
           FROM pg_class c WHERE c.oid=%s::regclass""",
        (TABLE,),
    )
    info = q.fetchone()
    if info is None or info[:5] != ("r", "postgres", False, 0, 0):
        raise RuntimeError(f"RELATION_OWNER_CONSUMER_DRIFT:{info!r}")
    q.execute(
        """SELECT external_symbol,market_source_id,count(*)
           FROM analytics.market_minutes GROUP BY 1,2 ORDER BY 1,2"""
    )
    groups = q.fetchall()
    expected = [(name, SOURCE_ID, ROWS_PER_SYMBOL) for name in PROTECTED]
    if groups != expected:
        raise RuntimeError("RETAINED_SEVEN_COHORTS_CHANGED")
    q.execute(
        """SELECT count(*),count(DISTINCT (
            market_source_id,instrument_id,observed_minute
        )) FROM analytics.market_minutes"""
    )
    if q.fetchone() != (ROWS, ROWS):
        raise RuntimeError("RETAINED_ROWS_OR_PKEY_CHANGED")
    q.execute(
        """SELECT count(*) FROM pg_index
           WHERE indrelid=%s::regclass AND indisvalid AND indisready""",
        (TABLE,),
    )
    if q.fetchone()[0] != INDEXES:
        raise RuntimeError("P90_MARKET_MINUTE_INDEX_SET_CHANGED")
    q.execute(
        """SELECT count(*) FROM pg_constraint
           WHERE conrelid=%s::regclass AND contype='p'""",
        (TABLE,),
    )
    if q.fetchone()[0] != 1:
        raise RuntimeError("P90_PRIMARY_KEY_MISSING")
    q.execute("SET TIME ZONE 'UTC'")
    q.execute("SET extra_float_digits=3")
    digest = hashlib.sha256()
    total_bytes = 0
    with q.copy(ROW_HASH_QUERY) as stream:
        for chunk in stream:
            digest.update(chunk)
            total_bytes += len(chunk)
    return {
        "rows": ROWS,
        "sha256": digest.hexdigest(),
        "copy_bytes": total_bytes,
        "relation_bytes": int(info[5]),
        "root_free_bytes": root_free(),
    }


def live_wal_bytes(q: Any) -> int:
    q.execute("SELECT coalesce(sum(size),0) FROM pg_ls_waldir()")
    return int(q.fetchone()[0])


def execute() -> int:
    import psycopg

    applying = "--apply" in sys.argv
    if len(sys.argv) != 2 or sys.argv[1] not in ("--check", "--apply"):
        raise RuntimeError("EXACT_CHECK_OR_APPLY_REQUIRED")
    if applying:
        import pwd

        if not hasattr(os, "geteuid"):
            raise RuntimeError("POSIX_ACTOR_REQUIRED")
        if os.geteuid() != pwd.getpwnam("postgres").pw_uid:
            raise RuntimeError("EXACT_POSTGRES_UNIX_ACTOR_REQUIRED")
    expected = proof()["source_evidence"]
    role = "postgres" if applying else "cripta"
    dsn = (
        f"dbname=cripta user={role} host=/var/run/postgresql "
        "connect_timeout=5 application_name=p90-minute-compact-642"
    )
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.cursor() as q:
            q.execute("SET lock_timeout='3000ms'")
            q.execute("SET statement_timeout='240000ms'")
            before = gate(q, applying=applying)
            if (before["sha256"], before["copy_bytes"]) != (
                expected["content_sha256"], expected["serialized_bytes"]
            ):
                raise RuntimeError("CURRENT_SOURCE_DIFFERS_FROM_RESTORED_PROOF")
            if not applying:
                print(json.dumps({"status": "CHECK", "before": before},
                                 sort_keys=True))
                return 0

            if before["root_free_bytes"] < MIN_FREE_BEFORE_BYTES:
                raise RuntimeError("SYSTEM_SSD_REWRITE_RESERVE_TOO_LOW")
            if before["relation_bytes"] < MIN_RELATION_BEFORE_BYTES:
                raise RuntimeError("SOURCE_ALREADY_COMPACTED_OR_DRIFTED")
            wal_before = live_wal_bytes(q)
            if wal_before > MAX_WAL_BEFORE_BYTES:
                raise RuntimeError("PRECOMPACT_WAL_TOO_LARGE")
            q.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (LOCK_KEY,))
            if q.fetchone()[0] is not True:
                raise RuntimeError("R12_CONCURRENT_WRITER_LOCK_HELD")
            try:
                q.execute("CHECKPOINT")
                if root_free() < MIN_FREE_BEFORE_BYTES:
                    raise RuntimeError("LOW_RESERVE_AFTER_CHECKPOINT")
                print(
                    json.dumps({"stage": "STARTING_SINGLE_RELATION_VACUUM",
                                "before": before, "wal_bytes": wal_before},
                               sort_keys=True),
                    flush=True,
                )
                # Physical-only rewrite. PostgreSQL rebuilds the existing
                # indexes; no index definitions, rows or FKs are dropped.
                q.execute("VACUUM (FULL, ANALYZE) analytics.market_minutes")
                after = gate(q, applying=True)
                if (after["sha256"], after["copy_bytes"]) != (
                    expected["content_sha256"], expected["serialized_bytes"]
                ):
                    raise RuntimeError("PROTECTED_ROWS_CHANGED_AFTER_REWRITE")
                if after["root_free_bytes"] < MIN_FREE_AFTER_BYTES:
                    raise RuntimeError("ROOT_SPACE_LOW_AFTER_REWRITE")
                q.execute("CHECKPOINT")
                print(
                    json.dumps({
                        "status": "DONE",
                        "before": before, "after": after,
                        "reclaimed_relation_bytes": (
                            before["relation_bytes"] - after["relation_bytes"]
                        ),
                        "wal_after_bytes": live_wal_bytes(q),
                    }, sort_keys=True),
                    flush=True,
                )
                return 0
            finally:
                q.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_KEY,)
                )


if __name__ == "__main__":
    try:
        raise SystemExit(execute())
    except Exception as exc:
        print(
            json.dumps({
                "status": "BLOCKED_OR_POST_REWRITE_UNKNOWN",
                "error_class": type(exc).__name__,
                "reason": str(exc)[:340],
            }, sort_keys=True),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
