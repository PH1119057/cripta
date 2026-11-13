#!/usr/bin/env python3
"""Issue #642: independent *scoped* PostgreSQL restore smoke on the Data SSD.

This validates binary-resident P90-linked context rows from a fresh pg_dump,
using a temporary, separate PostgreSQL 16 cluster. It is NOT a full
production-cluster recovery test and never authorizes LIVE or SQL deletion.

ACTOR/PATH CONTRACT (DEV 5.1 / 19.1-19.4)
Effective Unix actor: cripta (uid 107), DB source role cripta (SELECT only).
Read: this exact GitHub-reviewed file, production Unix socket, pg_dump,
      initdb, pg_ctl, pg_restore, source relation metadata.
Create/write: ONLY /data/cripta/research/p90_context_r12_proof_642
              and child files/cluster/socket by Unix cripta.
Delete/rename: NONE except temporary JSON manifest via atomic rename,
               scratch PostgreSQL's own internal files.
Source SQL writes: NONE. No sudo, no postgres actor substitution, no
production PostgreSQL restart, no network listener, no PGDATA touching.
Proof scope: two context tables and their 718 event links; no financial
execution table, secrets or full recovery. Completion must not be advertised
as full independent protected-cluster restoration.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

PARENT = Path("/data/cripta/research")
ROOT = PARENT / "p90_context_r12_proof_642"
CLUSTER = ROOT / "cluster"
SOCKET = ROOT / "socket"
ARCHIVE = ROOT / "p90_contexts_642.dump"
PG_LOG = ROOT / "scratch_pg.log"
RESULT = ROOT / "proof_result.json"
BIN = Path("/usr/lib/postgresql/16/bin")
SCRATCH_PORT = "55441"
TABLES = (
    "dispatcher_v2.coin_market_contexts",
    "mayak_v2.coin_market_contexts",
    "research_context.dispatcher_v2_event_links",
)
EXPECTED: dict[str, tuple[int, str]] = {
    "dispatcher_v2.coin_market_contexts":
        (450, "0346935967e32c973832634fbd90baa3"),
    "mayak_v2.coin_market_contexts":
        (450, "5ae861cf6c32dc9f9f05d05f48c6b6cc"),
}
EXPECTED_LINKS = 718
MIN_DATA_FREE = 3 * 1024 * 1024 * 1024
MAX_ARCHIVE_SIZE = 96 * 1024 * 1024


def run(argv: list[str], timeout: int = 80) -> str:
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "SUBPROCESS_FAILED:"
            + Path(argv[0]).name
            + ":" + str(result.returncode)
            + ":" + result.stderr[-700:]
        )
    return result.stdout[-1500:]


def guard_filesystem() -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 107:
        raise RuntimeError("UNIX_ACTOR_MUST_BE_CRIPTA")
    import pwd

    if pwd.getpwnam("cripta").pw_uid != 107:
        raise RuntimeError("CRITPA_ACTOR_IDENTITY_DRIFT")
    if not PARENT.is_dir() or not os.access(PARENT, os.W_OK | os.X_OK):
        raise RuntimeError("EXACT_DATA_DISK_PARENT_NOT_WRITABLE_BY_CRIPTA")
    if PARENT.stat().st_dev == Path("/").stat().st_dev:
        raise RuntimeError("PROOF_PATH_IS_SYSTEM_SSD")
    stats = os.statvfs(PARENT)
    if stats.f_bavail * stats.f_frsize < MIN_DATA_FREE:
        raise RuntimeError("INSUFFICIENT_DATA_DISK_FREE")
    if ROOT.exists() or ROOT.is_symlink():
        raise RuntimeError("PROOF_DIRECTORY_ALREADY_EXISTS")
    if not all(p.is_file() for p in (
        BIN / "pg_dump", BIN / "pg_restore", BIN / "initdb", BIN / "pg_ctl"
    )):
        raise RuntimeError("POSTGRES_16_BINARIES_MISSING")


def sql_connection(db: str, *, scratch: bool = False) -> psycopg.Connection:
    import psycopg

    return psycopg.connect(
        dbname=db,
        host=str(SOCKET) if scratch else "/var/run/postgresql",
        port=int(SCRATCH_PORT) if scratch else 5432,
        user="cripta",
        connect_timeout=5,
    )


def evidence(conn: psycopg.Connection) -> dict[str, Any]:
    outcome: dict[str, Any] = {}
    with conn.cursor() as q:
        q.execute("SET statement_timeout='15000ms'")
        for name in TABLES:
            # Strict whitelist, never caller-supplied relation.
            q.execute(
                "SELECT count(*),md5(string_agg("
                " md5(row_to_json(t)::text),'|' "
                " ORDER BY md5(row_to_json(t)::text)))"
                " FROM " + name + " t"
            )
            count, full_digest = q.fetchone()
            outcome[name] = {
                "rows": int(count),
                "full_row_digest": str(full_digest),
            }
            if name in EXPECTED:
                q.execute(
                    "SELECT md5(string_agg("
                    " coin_context_id||':'||content_hash,'|'"
                    " ORDER BY coin_context_id)) FROM " + name
                )
                fingerprint = q.fetchone()[0]
                if (count, fingerprint) != EXPECTED[name]:
                    raise RuntimeError(f"PROTECTED_CONTEXT_DRIFT:{name}")
                outcome[name]["content_digest"] = fingerprint
        if outcome[TABLES[2]]["rows"] != EXPECTED_LINKS:
            raise RuntimeError("EVENT_LINK_COUNT_DRIFT")
        q.execute(
            """SELECT count(DISTINCT coin_context_id)
               FROM research_context.dispatcher_v2_event_links
               WHERE coin_context_id IS NOT NULL"""
        )
        if q.fetchone()[0] != 450:
            raise RuntimeError("EVENT_LINK_IDS_DRIFT")
        q.execute(
            """SELECT count(*)
               FROM research_context.dispatcher_v2_event_links e
               LEFT JOIN dispatcher_v2.coin_market_contexts d
                 ON d.coin_context_id=e.coin_context_id
               WHERE e.coin_context_id IS NOT NULL
                 AND d.coin_context_id IS NULL"""
        )
        if q.fetchone()[0]:
            raise RuntimeError("EVENT_LINK_ORPHAN")
        q.execute(
            """SELECT count(*)
               FROM dispatcher_v2.coin_market_contexts d
               LEFT JOIN mayak_v2.coin_market_contexts m
                 ON m.coin_context_id=d.source_coin_context_id
               WHERE m.coin_context_id IS NULL"""
        )
        if q.fetchone()[0]:
            raise RuntimeError("MAYAK_CONTEXT_ORPHAN")
    return outcome


def save_result(data: dict[str, Any]) -> None:
    temporary = RESULT.with_suffix(".json.tmp")
    with temporary.open("x", encoding="utf-8") as output:
        json.dump(data, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(RESULT)


def main() -> int:
    guard_filesystem()
    with sql_connection("cripta") as live:
        with live.cursor() as q:
            q.execute("SELECT current_user,session_user,pg_is_in_recovery()")
            if q.fetchone() != ("cripta", "cripta", False):
                raise RuntimeError("PRODUCTION_SOURCE_ACTOR_INVALID")
        before = evidence(live)
    ROOT.mkdir(mode=0o750)
    SOCKET.mkdir(mode=0o700)
    CLUSTER.mkdir(mode=0o700)
    print(
        json.dumps({"stage": "PREFLIGHT", "counts": {
            t: v["rows"] for t, v in before.items()}}, sort_keys=True),
        flush=True,
    )

    # Fresh custom archive is made by source DB SELECT role, no production DML.
    run([
        str(BIN / "pg_dump"), "--format=custom", "--compress=6",
        "--no-owner", "--no-privileges", "--host=/var/run/postgresql",
        "--username=cripta", "--file=" + str(ARCHIVE),
        *["--table=" + t for t in TABLES], "cripta",
    ], timeout=120)
    if not 1024 < ARCHIVE.stat().st_size < MAX_ARCHIVE_SIZE:
        raise RuntimeError("TARGETED_BACKUP_ARCHIVE_SIZE_UNEXPECTED")
    sha = hashlib.sha256()
    with ARCHIVE.open("rb") as f:
        while block := f.read(1024 * 1024):
            sha.update(block)
    with sql_connection("cripta") as live:
        if evidence(live) != before:
            raise RuntimeError("PRODUCTION_SOURCE_CHANGED_DURING_BACKUP")
    print(
        json.dumps({"stage": "DUMP_WRITTEN", "bytes": ARCHIVE.stat().st_size,
                    "sha256": sha.hexdigest()}, sort_keys=True),
        flush=True,
    )

    # Isolated PostgreSQL cluster: separate PGDATA on Data disk, no TCP,
    # private Unix socket. It is never connected to production data files.
    run([
        str(BIN / "initdb"), "--pgdata=" + str(CLUSTER),
        "--username=cripta", "--auth-local=trust", "--auth-host=reject",
    ], timeout=75)
    with (CLUSTER / "postgresql.conf").open("a", encoding="utf-8") as config:
        config.write(
            "\nlisten_addresses = ''\n"
            "unix_socket_directories = '" + str(SOCKET) + "'\n"
            "unix_socket_permissions = 0700\n"
            "port = " + SCRATCH_PORT + "\n"
        )
    pgctl = str(BIN / "pg_ctl")
    started = False
    try:
        run([pgctl, "-D", str(CLUSTER), "-l", str(PG_LOG), "-w", "start"], 35)
        started = True
        with sql_connection("postgres", scratch=True) as scratch_admin:
            scratch_admin.autocommit = True
            scratch_admin.execute("CREATE DATABASE proof_642")
        with sql_connection("proof_642", scratch=True) as empty:
            for schema in ("dispatcher_v2", "mayak_v2", "research_context"):
                empty.execute("CREATE SCHEMA " + schema)
            empty.commit()
        restore = [
            str(BIN / "pg_restore"), "--no-owner", "--no-privileges",
            "--host=" + str(SOCKET), "--port=" + SCRATCH_PORT,
            "--username=cripta", "--dbname=proof_642",
        ]
        run(restore + ["--section=pre-data", str(ARCHIVE)], timeout=60)
        run(restore + ["--section=data", str(ARCHIVE)], timeout=60)

        with sql_connection("proof_642", scratch=True) as restored:
            after = evidence(restored)
        if before != after:
            raise RuntimeError("ISOLATED_RESTORE_HASH_MISMATCH")

        run([pgctl, "-D", str(CLUSTER), "-m", "fast", "-w", "stop"], 30)
        started = False
        run([pgctl, "-D", str(CLUSTER), "-l", str(PG_LOG), "-w", "start"], 35)
        started = True
        with sql_connection("proof_642", scratch=True) as reopened:
            if evidence(reopened) != before:
                raise RuntimeError("POST_RESTART_RESTORE_HASH_MISMATCH")

        save_result({
            "status": "SCOPED_INDEPENDENT_POSTGRES_RESTORE_PASS",
            "full_cluster_restore": False,
            "source_database": "cripta",
            "source_role": "cripta",
            "scratch_database": "proof_642",
            "restored_tables": before,
            "archive_sha256": sha.hexdigest(),
            "archive_bytes": ARCHIVE.stat().st_size,
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "source_backup_path": str(ARCHIVE),
        })
        print(
            json.dumps({"stage": "SCOPED_RESTORE_PASS",
                        "full_cluster_restore": False,
                        "archive_sha256": sha.hexdigest()}, sort_keys=True),
            flush=True,
        )
        return 0
    finally:
        if started:
            run([pgctl, "-D", str(CLUSTER), "-m", "fast", "-w", "stop"], 30)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps({"status": "RESTORE_PROOF_FAILED_OR_PARTIAL",
                        "error_class": type(exc).__name__,
                        "reason": str(exc)[:400]}, sort_keys=True),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
