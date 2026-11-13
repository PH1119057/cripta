#!/usr/bin/env python3
"""Issue #642: independent PostgreSQL16 snapshot restore, source SELECT only.

Unix and production PostgreSQL actor: cripta. File CREATE/WRITE only inside
/data/cripta/research/p90_mayak_snapshot_r12_proof_642 on Data SSD. No
production SQL mutation, no PGDATA/Exchange/trading writes or sudo/root.
Reclaims NO space. Generates scoped recoverability evidence for a later
separately approved physical-only VACUUM FULL of mayak_v2.snapshots.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

PARENT = Path("/data/cripta/research")
ROOT = PARENT / "p90_mayak_snapshot_r12_proof_642"
ARCHIVE = ROOT / "mayak_snapshots.dump"
CLUSTER = ROOT / "cluster"
SOCKET = ROOT / "socket"
LOG = ROOT / "scratch.log"
PROOF = ROOT / "proof_result.json"
BIN = Path("/usr/lib/postgresql/16/bin")
PORT = "55443"
TABLE = "mayak_v2.snapshots"
ROWS = 7883
INDEXES = 3
PINNED = 450
DATA_MIN_FREE = 7 * 1024**3
SOURCE_REF_TABLES = (
    ("dispatcher_v2.coin_market_contexts", "source_mayak_snapshot_id"),
    ("dispatcher_v2.global_market_contexts", "source_mayak_snapshot_id"),
    ("mayak_v2.coin_market_contexts", "mayak_snapshot_id"),
    ("mayak_v2.shared_market_contexts", "mayak_snapshot_id"),
)
EXPECTED_INBOUND_FK_COUNT = 9
ROW_COPY = (
    "COPY (SELECT row_to_json(t)::text FROM mayak_v2.snapshots t "
    "ORDER BY t.id) TO STDOUT WITH (FORMAT text)"
)


def command_checked(args: list[str], *, timeout: int) -> None:
    done = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(
            "SCRATCH_COMMAND_FAILED:" + Path(args[0]).name + ":"
            + str(done.returncode) + ":" + done.stderr[-430:]
        )


def connection(db: str, *, scratch: bool = False) -> psycopg.Connection:
    import psycopg

    return psycopg.connect(
        dbname=db, user="cripta",
        host=str(SOCKET) if scratch else "/var/run/postgresql",
        port=int(PORT) if scratch else 5432,
        connect_timeout=5,
        application_name="p90-snapshots-restore-642",
    )


def initial_actor_path() -> None:
    import pwd

    if os.geteuid() != pwd.getpwnam("cripta").pw_uid:
        raise RuntimeError("EXACT_CRIPTA_UNIX_ACTOR_REQUIRED")
    parent = os.lstat(PARENT)
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid():
        raise RuntimeError("WRONG_RESEARCH_DATA_PARENT_ACTOR")
    if not os.access(PARENT, os.W_OK | os.X_OK):
        raise RuntimeError("NO_DATA_PARENT_WRITE_PERMISSION")
    if PARENT.stat().st_dev == Path("/").stat().st_dev:
        raise RuntimeError("BACKUP_WOULD_LAND_ON_SYSTEM_SSD")
    if os.path.lexists(ROOT):
        raise RuntimeError("EXISTING_RESTORE_SCRATCH_DO_NOT_OVERWRITE")
    v = os.statvfs(PARENT)
    if v.f_bavail * v.f_frsize < DATA_MIN_FREE:
        raise RuntimeError("DATA_SSD_CAPACITY_TOO_LOW")
    if any(not (BIN / name).is_file() for name in
           ("pg_dump", "pg_restore", "pg_ctl", "initdb")):
        raise RuntimeError("PG16_RECOVERY_TOOLING_MISSING")


def evidence(conn: psycopg.Connection) -> dict[str, Any]:
    with conn.cursor() as q:
        q.execute("SELECT current_user,session_user,pg_is_in_recovery()")
        if q.fetchone() != ("cripta", "cripta", False):
            raise RuntimeError("DB_ACTOR_OR_RECOVERY_DRIFT")
        q.execute("SET TIME ZONE 'UTC'")
        q.execute("SET extra_float_digits=3")
        q.execute("SELECT count(*),count(DISTINCT id) FROM " + TABLE)
        if q.fetchone() != (ROWS, ROWS):
            raise RuntimeError("MAYAK_SNAPSHOT_ROWS_OR_PRIMARY_KEY_DRIFT")
        q.execute(
            "SELECT count(*),bool_and(indisvalid AND indisready) "
            "FROM pg_index WHERE indrelid=%s::regclass", (TABLE,),
        )
        if q.fetchone() != (INDEXES, True):
            raise RuntimeError("SNAPSHOT_THREE_INDEXES_CHANGED")
        q.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid=%s::regclass AND contype='p'", (TABLE,),
        )
        if q.fetchone()[0] != 1:
            raise RuntimeError("SNAPSHOT_PRIMARY_KEY_MISSING")
        q.execute(
            "SELECT count(*) FROM pg_constraint "
            "WHERE conrelid=%s::regclass AND contype='f'", (TABLE,),
        )
        if q.fetchone()[0] != 0:
            raise RuntimeError("SNAPSHOT_UNEXPECTED_OUTBOUND_FK")
        digest = hashlib.sha256()
        nbytes = 0
        with q.copy(ROW_COPY) as copied:
            for buf in copied:
                digest.update(buf)
                nbytes += len(buf)
        return {
            "rows": ROWS, "content_sha256": digest.hexdigest(),
            "serialized_bytes": nbytes, "indexes": INDEXES,
        }


def references(conn: psycopg.Connection) -> dict[str, Any]:
    from psycopg import sql

    samples: dict[str, Any] = {}
    union: set[int] = set()
    with conn.cursor() as q:
        q.execute(
            """SELECT n.nspname,r.relname,a.attname
               FROM pg_constraint c
               JOIN pg_class r ON r.oid=c.conrelid
               JOIN pg_namespace n ON n.oid=r.relnamespace
               JOIN pg_attribute a ON a.attrelid=c.conrelid
                                    AND a.attnum=c.conkey[1]
               WHERE c.contype='f'
                 AND c.confrelid=%s::regclass
                 AND array_length(c.conkey,1)=1
               ORDER BY n.nspname,r.relname,a.attname""",
            (TABLE,),
        )
        found = q.fetchall()
        names = {(n + "." + r, field) for n, r, field in found}
        if len(found) != EXPECTED_INBOUND_FK_COUNT or not (
            set(SOURCE_REF_TABLES) <= names
        ):
            raise RuntimeError("SNAPSHOT_INBOUND_FK_INVENTORY_CHANGED")
        for namespace, rel, field in found:
            name = namespace + "." + rel
            query = sql.SQL(
                "SELECT {field} FROM {relation} "
                "WHERE {field} IS NOT NULL ORDER BY {field}"
            ).format(
                field=sql.Identifier(field),
                relation=sql.Identifier(namespace, rel),
            )
            q.execute(query)
            rows = [int(x[0]) for x in q.fetchall()]
            expected = PINNED if (name, field) in SOURCE_REF_TABLES else 0
            if len(rows) != expected or len(set(rows)) != expected:
                raise RuntimeError("PROTECTED_CONTEXT_REFERENCE_DRIFT:" + name)
            sha = hashlib.sha256()
            for value in rows:
                sha.update(str(value).encode("ascii") + b"\\n")
            samples[name + "." + field] = {
                "count": len(rows), "sha256": sha.hexdigest(),
            }
            union.update(rows)
        if len(union) != PINNED:
            raise RuntimeError("EXPECTED_450_PROTECTED_SNAPSHOT_IDS")
        q.execute(
            "SELECT count(*) FROM " + TABLE + " WHERE id=ANY(%s)",
            (sorted(union),),
        )
        if q.fetchone()[0] != PINNED:
            raise RuntimeError("PROTECTED_SNAPSHOT_ID_MISSING")
    return {"by_fk": samples, "pinned_ids": sorted(union)}


def verify_pins_in_restored(
    conn: psycopg.Connection, refs: dict[str, Any],
) -> None:
    with conn.cursor() as q:
        q.execute(
            "SELECT count(*) FROM " + TABLE + " WHERE id=ANY(%s)",
            (refs["pinned_ids"],),
        )
        if q.fetchone()[0] != PINNED:
            raise RuntimeError("RESTORED_P90_SNAPSHOT_PINS_MISSING")


def sha256_archive() -> str:
    h = hashlib.sha256()
    with ARCHIVE.open("rb") as f:
        while block := f.read(4 * 1024 * 1024):
            h.update(block)
    return h.hexdigest()


def save_manifest(document: dict[str, Any]) -> None:
    temporary = PROOF.with_suffix(".json.tmp")
    with temporary.open("x", encoding="utf-8") as f:
        json.dump(document, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(PROOF)


def main() -> int:
    initial_actor_path()
    with connection("cripta") as production:
        original = evidence(production)
        original_links = references(production)
    ROOT.mkdir(mode=0o700)
    CLUSTER.mkdir(mode=0o700)
    SOCKET.mkdir(mode=0o700)
    print(json.dumps({"stage": "BEFORE", **original}), flush=True)
    command_checked([
        str(BIN / "pg_dump"), "--format=custom", "--compress=6",
        "--no-owner", "--no-privileges", "--host=/var/run/postgresql",
        "--username=cripta", "--table=" + TABLE,
        "--file=" + str(ARCHIVE), "cripta",
    ], timeout=360)
    nbytes = ARCHIVE.stat().st_size
    if not 4096 < nbytes < 1024**3:
        raise RuntimeError("SNAPSHOT_ARCHIVE_SIZE_INVALID")
    digest = sha256_archive()
    with connection("cripta") as production:
        if evidence(production) != original:
            raise RuntimeError("PRODUCTION_CHANGED_DURING_BACKUP")
        if references(production) != original_links:
            raise RuntimeError("SNAPSHOT_REFERENCES_CHANGED_DURING_BACKUP")
    command_checked([
        str(BIN / "initdb"), "--pgdata=" + str(CLUSTER),
        "--username=cripta", "--auth-local=trust", "--auth-host=reject",
    ], timeout=100)
    with (CLUSTER / "postgresql.conf").open("a", encoding="utf-8") as f:
        f.write(
            "\nlisten_addresses = ''\n"
            "unix_socket_directories = '" + str(SOCKET) + "'\n"
            "unix_socket_permissions = 0700\n"
            "port = " + PORT + "\n"
        )
    ctl = str(BIN / "pg_ctl")
    started = False
    try:
        command_checked([
            ctl, "-D", str(CLUSTER), "-l", str(LOG), "-w", "start",
        ], timeout=40)
        started = True
        with connection("postgres", scratch=True) as admin:
            admin.autocommit = True
            admin.execute("CREATE DATABASE proof_mayak_642")
        with connection("proof_mayak_642", scratch=True) as scratch:
            scratch.execute("CREATE SCHEMA mayak_v2")
            scratch.commit()
        command_checked([
            str(BIN / "pg_restore"), "--exit-on-error",
            "--no-owner", "--no-privileges",
            "--host=" + str(SOCKET), "--port=" + PORT,
            "--username=cripta", "--dbname=proof_mayak_642",
            str(ARCHIVE),
        ], timeout=360)
        with connection("proof_mayak_642", scratch=True) as scratch:
            restored = evidence(scratch)
            verify_pins_in_restored(scratch, original_links)
        if restored != original:
            raise RuntimeError("RESTORED_MAYAK_ROWS_SHA_MISMATCH")
        command_checked([
            ctl, "-D", str(CLUSTER), "-m", "fast", "-w", "stop",
        ], timeout=40)
        started = False
        command_checked([
            ctl, "-D", str(CLUSTER), "-l", str(LOG), "-w", "start",
        ], timeout=40)
        started = True
        with connection("proof_mayak_642", scratch=True) as scratch:
            if evidence(scratch) != original:
                raise RuntimeError("RESTARTED_SCRATCH_ROWS_DIFFER")
            verify_pins_in_restored(scratch, original_links)
        save_manifest({
            "status": "SCOPED_MAYAK_SNAPSHOT_RESTORE_PASS",
            "full_production_cluster_restore": False,
            "source_table": TABLE, "source_evidence": original,
            "restored_evidence": restored,
            "references": original_links,
            "archive_path": str(ARCHIVE),
            "archive_sha256": digest, "archive_bytes": nbytes,
            "created_at_utc": datetime.now(UTC).isoformat(),
        })
        print(json.dumps({
            "status": "SCOPED_MAYAK_SNAPSHOT_RESTORE_PASS",
            "rows": ROWS, "pinned": PINNED,
            "content_sha256": original["content_sha256"],
            "archive_sha256": digest, "archive_bytes": nbytes,
        }), flush=True)
        return 0
    finally:
        if started:
            command_checked([
                ctl, "-D", str(CLUSTER), "-m", "fast", "-w", "stop",
            ], timeout=40)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({
            "status": "MAYAK_SCOPED_RESTORE_FAILED",
            "error_class": type(exc).__name__,
            "reason": str(exc)[:420],
        }, sort_keys=True), file=sys.stderr)
        raise SystemExit(2) from exc
