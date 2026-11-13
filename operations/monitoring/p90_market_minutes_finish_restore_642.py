#!/usr/bin/env python3
"""Issue #642: complete an isolated minute-data restore with 2 referenced FKs.

The original minute archive was successfully restored as data into an
isolated PostgreSQL16 on the Data SSD but its first outbound FK failed
because identity reference tables were absent. This deterministic retry
reuses the *same* verified archive and stopped scratch cluster, adds a
separate tiny archive of exactly the three needed identity tables,
restores the 2 foreign keys, and independently checks all row SHA256s,
index/PK integrity and both FK definitions after a scratch restart.

Effective Unix/production DB actor cripta. Production SQL is SELECT only.
All CREATE/WRITE limited to existing
/data/cripta/research/p90_market_minutes_r12_proof_642 (Data SSD);
scratch PostgreSQL is the ONLY database altered. No sudo/root,
no production PGDATA, native, trading or financial action, no LIVE.
This is a scoped independent PostgreSQL restore, not full-cluster rollback.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg

SCRIPT = Path(__file__).with_name("p90_market_minutes_restore_642.py")
SPEC = importlib.util.spec_from_file_location("p90_original_minutes_642", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("ORIGINAL_REVIEWED_RESTORE_SOURCE_MISSING")
original = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(original)

ROOT = original.ROOT
MARKET_ARCHIVE = original.ARCHIVE
REF_ARCHIVE = ROOT / "identity_references.dump"
SCRATCH_RESULT = original.PROOF
MARKET_SHA256 = (
    "e719e977a7302401f31ff94f316eb37204d3d5e508a3bb5e72b346d58242ac9b"
)
MARKET_ARCHIVE_BYTES = 248268366
EXPECTED_MARKET_HASH = (
    "59fd25feae5e28ae20d85cfb479e51ef6ebad99950e9708da0642110ac96b926"
)
REFERENCE_TABLES = (
    ("identity.venues", "venue_id", 1),
    ("identity.market_sources", "market_source_id", 1),
    ("identity.instruments", "instrument_id", 40),
)
FKS = (
    (
        "market_minutes_instrument_id_venue_id_product_category_ext_fkey",
        "FOREIGN KEY (instrument_id, venue_id, product_category, external_symbol) "
        "REFERENCES identity.instruments(instrument_id, venue_id, "
        "product_category, external_symbol)",
    ),
    (
        "market_minutes_market_source_id_venue_id_fkey",
        "FOREIGN KEY (market_source_id, venue_id) "
        "REFERENCES identity.market_sources(market_source_id, venue_id)",
    ),
)
DATA_MIN_FREE = 5 * 1024**3


def sha256_file(path: Path) -> str:
    hash_value = hashlib.sha256()
    with path.open("rb") as infile:
        while part := infile.read(4 * 1024 * 1024):
            hash_value.update(part)
    return hash_value.hexdigest()


def filesystem_guard() -> None:
    if not hasattr(os, "geteuid") or os.geteuid() != 107:
        raise RuntimeError("EXACT_CRIPTA_UNIX_ACTOR_REQUIRED")
    import pwd

    if pwd.getpwnam("cripta").pw_uid != 107:
        raise RuntimeError("CRIPTA_ACTOR_IDENTITY_DRIFT")
    for path in (ROOT, original.CLUSTER, original.SOCKET, MARKET_ARCHIVE):
        s = os.lstat(path)
        if s.st_uid != 107 or stat.S_ISLNK(s.st_mode):
            raise RuntimeError(f"UNTRUSTED_SCRATCH_PATH:{path}")
    if original.CLUSTER.joinpath("postmaster.pid").exists():
        raise RuntimeError("SCRATCH_POSTGRES_ALREADY_RUNNING")
    if SCRATCH_RESULT.exists() or REF_ARCHIVE.exists():
        raise RuntimeError("RECOVERY_ALREADY_DONE_OR_RETRY_UNSAFE")
    if MARKET_ARCHIVE.stat().st_size != MARKET_ARCHIVE_BYTES:
        raise RuntimeError("ORIGINAL_MARKET_DUMP_SIZE_DRIFT")
    if sha256_file(MARKET_ARCHIVE) != MARKET_SHA256:
        raise RuntimeError("ORIGINAL_MARKET_DUMP_SHA256_DRIFT")
    if original.PARENT.stat().st_dev == Path("/").stat().st_dev:
        raise RuntimeError("RECOVERY_SCRATCH_ON_ROOT_DEVICE")
    st = os.statvfs(ROOT)
    if st.f_bavail * st.f_frsize < DATA_MIN_FREE:
        raise RuntimeError("DATA_SSD_LOW")
    if not os.access(ROOT, os.X_OK | os.W_OK):
        raise RuntimeError("SCRATCH_WRITER_ACTOR_DENIED")


def refs(conn: psycopg.Connection) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with conn.cursor() as q:
        q.execute("SET TIME ZONE 'UTC'")
        q.execute("SET extra_float_digits=3")
        for table, primary_key, expected in REFERENCE_TABLES:
            digest = hashlib.sha256()
            copied_bytes = 0
            sql = (
                "COPY (SELECT row_to_json(t)::text FROM " + table
                + " t ORDER BY t." + primary_key + ") "
                "TO STDOUT WITH (FORMAT text)"
            )
            with q.copy(sql) as stream:
                for chunk in stream:
                    digest.update(chunk)
                    copied_bytes += len(chunk)
            q.execute("SELECT count(*) FROM " + table)
            count = q.fetchone()[0]
            if count != expected:
                raise RuntimeError(f"REFERENCE_IDENTITY_COUNT_DRIFT:{table}")
            out[table] = {
                "rows": count,
                "sha256": digest.hexdigest(),
                "serialized_bytes": copied_bytes,
            }
    return out


def validate_fk_defs(conn: psycopg.Connection, *, source: bool) -> None:
    with conn.cursor() as q:
        q.execute(
            """SELECT conname,pg_get_constraintdef(oid)
               FROM pg_constraint
               WHERE conrelid='analytics.market_minutes'::regclass
                 AND contype='f'
               ORDER BY conname"""
        )
        rows = q.fetchall()
    if rows != sorted(FKS):
        raise RuntimeError(
            f"{'SOURCE' if source else 'SCRATCH'}_MINUTES_FK_DRIFT:{rows!r}"
        )


def manifest(data: dict[str, Any]) -> None:
    temporary = SCRATCH_RESULT.with_suffix(".json.tmp")
    with temporary.open("x", encoding="utf-8") as f:
        json.dump(data, f, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    temporary.replace(SCRATCH_RESULT)


def run() -> int:
    filesystem_guard()
    with original.connection("cripta") as production:
        before = original.evidence(production, source=True)
        reference_before = refs(production)
        validate_fk_defs(production, source=True)
    if before["content_sha256"] != EXPECTED_MARKET_HASH:
        raise RuntimeError("SOURCE_MARKET_MINUTES_HASH_DRIFT")

    original.subprocess_checked(
        [
            str(original.PG_BIN / "pg_dump"), "--format=custom",
            "--compress=6", "--no-owner", "--no-privileges",
            "--host=/var/run/postgresql", "--username=cripta",
            "--file=" + str(REF_ARCHIVE),
            *["--table=" + table for table, _pk, _n in REFERENCE_TABLES],
            "cripta",
        ],
        timeout=80,
    )
    if not 1024 < REF_ARCHIVE.stat().st_size < 10 * 1024 * 1024:
        raise RuntimeError("IDENTITY_ARCHIVE_SIZE_INVALID")
    ref_sha = sha256_file(REF_ARCHIVE)
    with original.connection("cripta") as production:
        if original.evidence(production, source=True) != before:
            raise RuntimeError("MINUTES_CHANGED_DURING_IDENTITY_BACKUP")
        if refs(production) != reference_before:
            raise RuntimeError("IDENTITY_CHANGED_DURING_BACKUP")

    ctl = str(original.PG_BIN / "pg_ctl")
    started = False
    try:
        original.subprocess_checked(
            [
                ctl, "-D", str(original.CLUSTER), "-l",
                str(original.SCRATCH_LOG), "-w", "start",
            ],
            timeout=40,
        )
        started = True
        with original.connection("proof_minutes_642", scratch=True) as db:
            with db.cursor() as q:
                q.execute("SELECT to_regnamespace('identity') IS NULL")
                if q.fetchone()[0] is not True:
                    raise RuntimeError("SCRATCH_IDENTITY_ALREADY_EXISTS")
                q.execute(
                    """SELECT count(*) FROM pg_constraint
                       WHERE conrelid='analytics.market_minutes'::regclass
                         AND contype='f'"""
                )
                if q.fetchone()[0] != 0:
                    raise RuntimeError("FAILED_FIRST_RESTORE_STATE_DRIFT")
                q.execute("CREATE SCHEMA identity")
                db.commit()

        original.subprocess_checked(
            [
                str(original.PG_BIN / "pg_restore"), "--exit-on-error",
                "--no-owner", "--no-privileges",
                "--host=" + str(original.SOCKET), "--port=" + original.PORT,
                "--username=cripta", "--dbname=proof_minutes_642",
                str(REF_ARCHIVE),
            ],
            timeout=80,
        )
        with original.connection("proof_minutes_642", scratch=True) as db:
            if refs(db) != reference_before:
                raise RuntimeError("RESTORED_REFERENCED_IDENTITY_DIFFERS")
            with db.cursor() as q:
                for name, definition in FKS:
                    q.execute(
                        "ALTER TABLE ONLY analytics.market_minutes "
                        "ADD CONSTRAINT " + name + " " + definition
                    )
            db.commit()
            validate_fk_defs(db, source=False)
            restored = original.evidence(db, source=False)
            if {k: v for k, v in restored.items() if k != "source"} != {
                k: v for k, v in before.items() if k != "source"
            }:
                raise RuntimeError("RESTORED_MARKET_MINUTE_ROW_SHA_DIFFERS")

        original.subprocess_checked(
            [ctl, "-D", str(original.CLUSTER), "-m", "fast", "-w", "stop"],
            timeout=40,
        )
        started = False
        original.subprocess_checked(
            [
                ctl, "-D", str(original.CLUSTER), "-l",
                str(original.SCRATCH_LOG), "-w", "start",
            ],
            timeout=40,
        )
        started = True
        with original.connection("proof_minutes_642", scratch=True) as db:
            if original.evidence(db, source=False) != restored:
                raise RuntimeError("POST_RESTART_MARKET_MINUTES_DIFFER")
            if refs(db) != reference_before:
                raise RuntimeError("POST_RESTART_REFERENCE_ROWS_DIFFER")
            validate_fk_defs(db, source=False)
        manifest({
            "status": "SCOPED_MARKET_MINUTES_RESTORE_PASS",
            "full_production_cluster_restore": False,
            "source_table": original.TABLE,
            "source_evidence": before,
            "restored_evidence": restored,
            "archive_sha256": MARKET_SHA256,
            "archive_bytes": MARKET_ARCHIVE_BYTES,
            "archive_path": str(MARKET_ARCHIVE),
            "identity_reference_archive_sha256": ref_sha,
            "identity_reference_archive_path": str(REF_ARCHIVE),
            "identity_reference_evidence": reference_before,
            "validated_outbound_foreign_keys": [name for name, _ in FKS],
            "at_utc": datetime.now(UTC).isoformat(),
        })
        print(
            json.dumps({
                "status": "SCOPED_MARKET_MINUTES_RESTORE_PASS",
                "rows": before["rows"],
                "sha256": before["content_sha256"],
                "identity_reference_sha256": ref_sha,
                "foreign_keys": len(FKS),
            }, sort_keys=True),
            flush=True,
        )
        return 0
    finally:
        if started:
            original.subprocess_checked(
                [
                    ctl, "-D", str(original.CLUSTER),
                    "-m", "fast", "-w", "stop",
                ],
                timeout=40,
            )


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except Exception as error:
        print(
            json.dumps({
                "status": "SCOPED_FK_RESTORE_FAILED",
                "error_class": type(error).__name__,
                "reason": str(error)[:430],
            }, sort_keys=True),
            file=sys.stderr,
        )
        raise SystemExit(2) from error
