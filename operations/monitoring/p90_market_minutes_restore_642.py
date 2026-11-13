#!/usr/bin/env python3
"""Issue #642: full-row independent restore of the seven retained market minutes.

READ: only the current analytics.market_minutes via cripta SELECT, GitHub-
reviewed script and PostgreSQL 16 binaries. Production SQL writes: NONE.
Actor: Unix cripta (uid from pwd) and source DB cripta. File CREATE/WRITE:
only /data/cripta/research/p90_market_minutes_r12_proof_642, owned by cripta.
File DELETE outside scratch PostgreSQL internals: NONE. No root/sudo, no
production server restart, no network listener, no trading/runtime changes.
This is a *scoped* data/index restore, NOT whole-cluster rollback evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

PARENT = Path("/data/cripta/research")
ROOT = PARENT / "p90_market_minutes_r12_proof_642"
ARCHIVE = ROOT / "market_minutes.dump"
CLUSTER = ROOT / "cluster"
SOCKET = ROOT / "socket"
SCRATCH_LOG = ROOT / "scratch.log"
PROOF = ROOT / "proof_result.json"
PG_BIN = Path("/usr/lib/postgresql/16/bin")
PORT = "55442"
TABLE = "analytics.market_minutes"
EXPECTED_SOURCE = "ms-bybit-mainnet-public-v1"
PROTECTED = (
    "APTUSDT", "ARBUSDT", "BTCUSDT", "DOTUSDT", "ETHUSDT",
    "INJUSDT", "LTCUSDT",
)
ROWS_PER_SYMBOL = 69110
EXPECTED_ROWS = 483770
MIN_DATA_FREE = 6 * 1024 * 1024 * 1024
MAX_ARCHIVE_BYTES = 3 * 1024 * 1024 * 1024
ROW_HASH_QUERY = (
    "COPY (SELECT row_to_json(t)::text FROM analytics.market_minutes t "
    "ORDER BY t.market_source_id, t.instrument_id, t.observed_minute) "
    "TO STDOUT WITH (FORMAT text)"
)


def subprocess_checked(command: list[str], *, timeout: int) -> None:
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "ISOLATED_STEP_FAILURE:"
            + Path(command[0]).name
            + ":" + str(result.returncode)
            + ":" + result.stderr[-480:]
        )


def filesystem_preflight() -> None:
    if not hasattr(os, "geteuid"):
        raise RuntimeError("POSIX_ACTOR_REQUIRED")
    if os.geteuid() != 107:
        raise RuntimeError("EXACT_CRIPTA_UNIX_ACTOR_REQUIRED")
    import pwd

    if pwd.getpwnam("cripta").pw_uid != 107:
        raise RuntimeError("CRIPTA_UID_DRIFT")
    if not PARENT.is_dir() or not os.access(PARENT, os.W_OK | os.X_OK):
        raise RuntimeError("DATA_PARENT_NOT_WRITABLE_AS_CRIPTA")
    if PARENT.stat().st_dev == Path("/").stat().st_dev:
        raise RuntimeError("DATA_PROOF_ON_SYSTEM_DISK")
    if ROOT.exists() or ROOT.is_symlink():
        raise RuntimeError("PREEXISTING_PROOF_DIRECTORY")
    st = os.statvfs(PARENT)
    if st.f_bavail * st.f_frsize < MIN_DATA_FREE:
        raise RuntimeError("INSUFFICIENT_DATA_DISK_CAPACITY")
    required = ("pg_dump", "pg_restore", "initdb", "pg_ctl")
    if any(not (PG_BIN / name).is_file() for name in required):
        raise RuntimeError("POSTGRES_16_TOOLS_MISSING")


def connection(db: str, *, scratch: bool = False) -> psycopg.Connection:
    import psycopg

    return psycopg.connect(
        dbname=db, user="cripta",
        host=str(SOCKET) if scratch else "/var/run/postgresql",
        port=int(PORT) if scratch else 5432,
        connect_timeout=5,
        application_name="p90-market-minutes-restore-642",
    )


def evidence(conn: psycopg.Connection, *, source: bool) -> dict[str, Any]:
    with conn.cursor() as cur:
        cur.execute("SELECT current_user,session_user,pg_is_in_recovery()")
        actor, session, recovery = cur.fetchone()
        if (actor, session, recovery) != ("cripta", "cripta", False):
            raise RuntimeError("DATABASE_ACTOR_OR_RECOVERY_DRIFT")
        cur.execute("SET TIME ZONE 'UTC'")
        cur.execute("SET extra_float_digits=3")
        cur.execute(
            """SELECT external_symbol,market_source_id,count(*)
               FROM analytics.market_minutes GROUP BY 1,2 ORDER BY 1,2"""
        )
        groups = cur.fetchall()
        expected = sorted(
            (name, EXPECTED_SOURCE, ROWS_PER_SYMBOL) for name in PROTECTED
        )
        if groups != expected:
            raise RuntimeError(f"PROTECTED_COHORT_DRIFT:{groups!r}")
        cur.execute(
            """SELECT count(*),count(DISTINCT (
                market_source_id,instrument_id,observed_minute
            )) FROM analytics.market_minutes"""
        )
        count, distinct_pk = cur.fetchone()
        if (count, distinct_pk) != (EXPECTED_ROWS, EXPECTED_ROWS):
            raise RuntimeError("ROWS_OR_PRIMARY_KEY_DRIFT")
        cur.execute(
            """SELECT count(*) FROM pg_constraint
               WHERE conrelid='analytics.market_minutes'::regclass
                 AND contype='p'"""
        )
        if cur.fetchone()[0] != 1:
            raise RuntimeError("MISSING_PRIMARY_KEY")
        cur.execute(
            """SELECT count(*) FROM pg_index
               WHERE indrelid='analytics.market_minutes'::regclass
                 AND indisvalid AND indisready"""
        )
        if cur.fetchone()[0] != 3:
            raise RuntimeError("INDEX_DEFINITION_DRIFT")
        cur.execute(
            """SELECT count(*) FROM pg_constraint
               WHERE confrelid='analytics.market_minutes'::regclass
                 AND contype='f'"""
        )
        if cur.fetchone()[0]:
            raise RuntimeError("UNEXPECTED_REFERENCING_CHILD")
        cur.execute(
            """SELECT count(*) FROM pg_trigger
               WHERE tgrelid='analytics.market_minutes'::regclass
                 AND NOT tgisinternal"""
        )
        if cur.fetchone()[0]:
            raise RuntimeError("UNEXPECTED_CUSTOM_TRIGGER")
        sha = hashlib.sha256()
        copied_bytes = 0
        with cur.copy(ROW_HASH_QUERY) as stream:
            for data in stream:
                sha.update(data)
                copied_bytes += len(data)
        return {
            "rows": count,
            "unique_pk": distinct_pk,
            "groups": [[s, source_id, n] for s, source_id, n in groups],
            "content_sha256": sha.hexdigest(),
            "serialized_bytes": copied_bytes,
            "source": source,
        }


def write_manifest(data: dict[str, Any]) -> None:
    tmp = PROOF.with_suffix(".json.tmp")
    with tmp.open("x", encoding="utf-8") as out:
        json.dump(data, out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
    tmp.replace(PROOF)


def run() -> int:
    filesystem_preflight()
    with connection("cripta") as production:
        before = evidence(production, source=True)
    ROOT.mkdir(mode=0o700)
    SOCKET.mkdir(mode=0o700)
    CLUSTER.mkdir(mode=0o700)
    print(
        json.dumps({"stage": "BEFORE", "rows": before["rows"],
                    "content_sha256": before["content_sha256"]}),
        flush=True,
    )

    subprocess_checked(
        [
            str(PG_BIN / "pg_dump"), "--format=custom", "--compress=6",
            "--no-owner", "--no-privileges", "--host=/var/run/postgresql",
            "--username=cripta", "--file=" + str(ARCHIVE),
            "--table=" + TABLE, "cripta",
        ],
        timeout=360,
    )
    archive_size = ARCHIVE.stat().st_size
    if not 4096 < archive_size < MAX_ARCHIVE_BYTES:
        raise RuntimeError("ARCHIVE_SIZE_DRIFT")
    archive_sha = hashlib.sha256()
    with ARCHIVE.open("rb") as f:
        while block := f.read(4 * 1024 * 1024):
            archive_sha.update(block)
    with connection("cripta") as production:
        if evidence(production, source=True) != before:
            raise RuntimeError("SOURCE_CHANGED_AFTER_DUMP")
    print(
        json.dumps({"stage": "ARCHIVE_VERIFIED", "archive_bytes": archive_size,
                    "archive_sha256": archive_sha.hexdigest()}), flush=True,
    )

    subprocess_checked(
        [
            str(PG_BIN / "initdb"), "--pgdata=" + str(CLUSTER),
            "--username=cripta", "--auth-local=trust", "--auth-host=reject",
        ],
        timeout=100,
    )
    with (CLUSTER / "postgresql.conf").open("a", encoding="utf-8") as f:
        f.write(
            "\nlisten_addresses = ''\n"
            "unix_socket_directories = '" + str(SOCKET) + "'\n"
            "unix_socket_permissions = 0700\n"
            "port = " + PORT + "\n"
        )
    ctl = str(PG_BIN / "pg_ctl")
    started = False
    try:
        subprocess_checked(
            [ctl, "-D", str(CLUSTER), "-l", str(SCRATCH_LOG),
             "-w", "start"],
            timeout=40,
        )
        started = True
        with connection("postgres", scratch=True) as scratch_admin:
            scratch_admin.autocommit = True
            scratch_admin.execute("CREATE DATABASE proof_minutes_642")
        with connection("proof_minutes_642", scratch=True) as scratch:
            scratch.execute("CREATE SCHEMA analytics")
            scratch.commit()
        subprocess_checked(
            [
                str(PG_BIN / "pg_restore"), "--exit-on-error",
                "--no-owner", "--no-privileges",
                "--host=" + str(SOCKET), "--port=" + PORT,
                "--username=cripta", "--dbname=proof_minutes_642",
                str(ARCHIVE),
            ],
            timeout=360,
        )
        with connection("proof_minutes_642", scratch=True) as scratch:
            restored = evidence(scratch, source=False)
        if {k: v for k, v in restored.items() if k != "source"} != {
            k: v for k, v in before.items() if k != "source"
        }:
            raise RuntimeError("INDEPENDENT_RESTORE_CONTENT_DIFFERS")

        subprocess_checked(
            [ctl, "-D", str(CLUSTER), "-m", "fast", "-w", "stop"],
            timeout=40,
        )
        started = False
        subprocess_checked(
            [ctl, "-D", str(CLUSTER), "-l", str(SCRATCH_LOG),
             "-w", "start"],
            timeout=40,
        )
        started = True
        with connection("proof_minutes_642", scratch=True) as reopened:
            second = evidence(reopened, source=False)
        if second != restored:
            raise RuntimeError("RESTORED_ROWS_DIFFER_AFTER_SCRATCH_RESTART")
        write_manifest({
            "status": "SCOPED_MARKET_MINUTES_RESTORE_PASS",
            "full_production_cluster_restore": False,
            "source_table": TABLE,
            "source_evidence": before,
            "restored_evidence": second,
            "archive_sha256": archive_sha.hexdigest(),
            "archive_bytes": archive_size,
            "archive_path": str(ARCHIVE),
            "at_utc": datetime.now(UTC).isoformat(),
        })
        print(
            json.dumps({"status": "SCOPED_MARKET_MINUTES_RESTORE_PASS",
                        "rows": EXPECTED_ROWS,
                        "source_sha256": before["content_sha256"],
                        "archive_sha256": archive_sha.hexdigest()}),
            flush=True,
        )
        return 0
    finally:
        if started:
            subprocess_checked(
                [ctl, "-D", str(CLUSTER), "-m", "fast", "-w", "stop"],
                timeout=40,
            )


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except Exception as exc:
        print(
            json.dumps({
                "status": "RESTORE_FAILED_OR_UNKNOWN",
                "error_class": type(exc).__name__,
                "reason": str(exc)[:420],
            }, sort_keys=True),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
