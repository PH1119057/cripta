#!/usr/bin/env python3
"""#865: fixed one-index migration rail, not a general PostgreSQL console.

OS owner root holds the existing release lock and reads fixed hash-pinned SQL
and the verified backup. The ONLY database actor is Unix/DB postgres via
local peer-authenticated PG16 psql. No shell, caller-selected SQL/path,
production service lifecycle, grants, data DML, financial or Exchange action.
Install this exact reviewed file root-owned under /usr/local/libexec.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

INSTALLED = Path("/usr/local/libexec/cripta-p90-account-generation-index-865.py")
BASE = "af705ad3b9f2af77cced472917c6ba244f500295"
TEST_ROOT = Path("/data/cripta/research/testenvs/p90_causal_candidate_865")
CANDIDATE = TEST_ROOT / BASE
C2_RECEIPT = TEST_ROOT / "runs/20261128T154240Z_0f8e345848.json"
C2_HASH = "7462e33f372e8dd445db7e2803160fa44c800b2ce20ba13cafcb821b6e6fa149"
UP = CANDIDATE / "operations/sql/20261128_p90_account_generation_lookup_index.sql"
DOWN = CANDIDATE / "operations/sql/rollback/20261128_p90_account_generation_lookup_index.sql"
UP_HASH = "0f0cf7e2ff3b2c49aa10ceb5e481b63e22c6f5558e24282ec4ffa26c51b8caaf"
DOWN_HASH = "6dbc44a66ace4e4c6ad4529fc7cb86149ef8c64a18100a354837bcb380f6042b"
LOCK = Path("/run/lock/cripta-install-verified-release.lock")
PGDATA = Path("/var/lib/postgresql/16/main")
BACKUPS = Path("/data/cripta/backups/system")
LATEST = Path("/var/lib/cripta/backup/latest.json")
INDEX = "ix_account_state_generations_proven_terminal_account_started"
DEFINITION = (
    "CREATE INDEX " + INDEX + " ON runtime.account_state_generations USING btree "
    "(trading_account_id, account_connector_id, started_at DESC, generation_id DESC) "
    "WHERE ((identity_status = 'PROVEN'::text) AND "
    "(state = ANY (ARRAY['COMPLETE'::text, 'FAILED'::text])))"
)
MIB, GIB = 1024**2, 1024**3
ENV = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "PGCONNECT_TIMEOUT": "5"}
PSQL = (
    "/usr/sbin/runuser", "-u", "postgres", "--", "/usr/bin/psql",
    "-X", "-w", "-h", "/var/run/postgresql", "-p", "5432", "-U", "postgres",
    "-d", "cripta", "-v", "ON_ERROR_STOP=1", "-qAt",
)
PROBE = b"""
SELECT json_build_object(
    'role',current_user,'session',session_user,'db',current_database(),
    'local',inet_server_addr() IS NULL,'recovery',pg_is_in_recovery(),
    'version',current_setting('server_version_num')::int,
    'pgdata',current_setting('data_directory'),'port',current_setting('port')::int,
    'owner',pg_get_userbyid(c.relowner),'kind',c.relkind,
    'heap_bytes',pg_relation_size(c.oid),
    'rows',greatest(c.reltuples::bigint,
        coalesce((SELECT n_live_tup FROM pg_stat_user_tables WHERE relid=c.oid),0)),
    'max_wal_bytes',pg_size_bytes(current_setting('max_wal_size')),
    'replication_slots',(SELECT count(*) FROM pg_replication_slots),
    'archive_mode',current_setting('archive_mode'),
    'long_xacts',(SELECT count(*) FROM pg_stat_activity
        WHERE pid<>pg_backend_pid() AND xact_start<clock_timestamp()-interval '30 seconds'),
    'index_builds',(SELECT count(*) FROM pg_stat_progress_create_index),
    'gate',(SELECT enabled FROM control.execution_gates WHERE mode='mainnet'),
    'permissions',(SELECT count(*) FROM strategy_entry.execution_permissions WHERE enabled=true),
    'arms',(SELECT count(*) FROM control.live_arm_sessions WHERE state='ACTIVE'),
    'account_arms',(SELECT count(*) FROM control.account_live_arm_sessions WHERE state='ACTIVE'),
    'grants',ARRAY[has_table_privilege('cripta',c.oid,'SELECT'),
        has_table_privilege('cripta',c.oid,'INSERT'),
        has_table_privilege('cripta',c.oid,'UPDATE'),
        has_table_privilege('cripta',c.oid,'DELETE')],
    'index',(SELECT json_build_object('definition',pg_get_indexdef(i.indexrelid),
        'valid',i.indisvalid,'ready',i.indisready,'live',i.indislive,
        'bytes',pg_relation_size(i.indexrelid))
        FROM pg_index i WHERE i.indexrelid=to_regclass(
          'runtime.ix_account_state_generations_proven_terminal_account_started'))
)
FROM pg_class c WHERE c.oid=to_regclass('runtime.account_state_generations');
"""


class Blocked(RuntimeError):
    pass


def require(ok: bool, code: str) -> None:
    if not ok:
        raise Blocked(code)


def parse_mode(args: list[str]) -> str:
    require(args in (["--check"], ["--apply"], ["--rollback"]), "EXACT_MODE_REQUIRED")
    return args[0]


def read_bytes(path: Path, limit: int = 65536, digest: str | None = None,
               root_owned: bool = False) -> bytes:
    require(path.resolve() == path, "FIXED_PATH_SYMLINK")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= limit,
                "FIXED_FILE_NOT_BOUNDED")
        if root_owned:
            require(info.st_uid == 0 and info.st_mode & 0o022 == 0,
                    "ROOT_FILE_OWNER_MODE")
        data = stream.read(limit + 1)
        require(len(data) == info.st_size, "FIXED_FILE_CHANGED")
    if digest is not None:
        require(hashlib.sha256(data).hexdigest() == digest, "FIXED_BYTES_HASH_MISMATCH")
    return data


def verify_installed() -> None:
    require(sys.platform == "linux" and os.geteuid() == 0, "ROOT_OS_RAIL_REQUIRED")
    require(Path(__file__) == INSTALLED and INSTALLED.resolve() == INSTALLED,
            "INSTALLED_RAIL_PATH_REQUIRED")
    for path in (INSTALLED.parent, INSTALLED):
        info = path.lstat()
        require(info.st_uid == 0 and info.st_gid == 0 and info.st_mode & 0o022 == 0,
                "INSTALLED_RAIL_OWNER_MODE")
    require(stat.S_ISREG(INSTALLED.lstat().st_mode), "INSTALLED_RAIL_NOT_REGULAR")


def source_bytes() -> tuple[bytes, bytes]:
    proof = json.loads(read_bytes(C2_RECEIPT, digest=C2_HASH))
    require(proof.get("expected_commit") == BASE and proof.get("state") == "PASS"
            and proof.get("candidate_root") == str(CANDIDATE)
            and all(proof.get(k) is False for k in (
                "prod_db_changed", "prod_services_changed", "prod_symlink_changed",
                "exchange_mutation")), "C2_RECEIPT_UNVERIFIED")
    return read_bytes(UP, digest=UP_HASH), read_bytes(DOWN, digest=DOWN_HASH)


def psql(data: bytes, *, readonly: bool, timeout: int) -> str:
    options = ("-c default_transaction_read_only=on -c statement_timeout=5000 "
               "-c lock_timeout=3000" if readonly else
               "-c maintenance_work_mem=64MB -c max_parallel_maintenance_workers=0")
    try:
        result = subprocess.run(
            PSQL, input=data, capture_output=True, cwd="/",
            env={**ENV, "PGOPTIONS": options}, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise Blocked("PSQL_OUTCOME_UNKNOWN_RECONCILE_NO_RETRY") from exc
    require(result.returncode == 0, "PSQL_REJECTED_RECONCILE_INDEX_STATE")
    return result.stdout.decode("utf-8").strip()


def probe() -> dict:
    return json.loads(psql(PROBE, readonly=True, timeout=12))


def validate(proof: dict, *, allow_invalid: bool = False) -> None:
    require(all(proof.get(k) == v for k, v in {
        "role": "postgres", "session": "postgres", "db": "cripta",
        "local": True, "recovery": False, "pgdata": str(PGDATA), "port": 5432,
        "owner": "postgres", "kind": "r", "gate": 0, "permissions": 0,
        "arms": 0, "account_arms": 0, "grants": [True, True, True, False],
    }.items()) and 160000 <= proof.get("version", 0) < 170000,
            "DB_ACTOR_SCHEMA_OR_FINANCIAL_STATE_UNVERIFIED")
    idx = proof.get("index")
    if idx is not None:
        require(idx.get("definition") == DEFINITION, "INDEX_DEFINITION_CONFLICT")
        require(allow_invalid or all(idx.get(k) is True for k in
                ("valid", "ready", "live")), "INDEX_INVALID_RECONCILE_ROLLBACK")


def budget(proof: dict, available: int) -> dict:
    require(type(proof.get("rows")) is int and proof["rows"] > 0
            and type(proof.get("max_wal_bytes")) is int and proof["max_wal_bytes"] > 0,
            "INDEX_BUDGET_STATS_UNAVAILABLE")
    # 256 bytes per estimated live row vs ~143 B observed in width-matched
    # synthetic regression; 2x index temporary and 2x build WAL allowances,
    # plus ordinary WAL soft target and 1 GiB residual reserve.
    idx = max(192 * MIB, proof["rows"] * 256)
    reserve = max(GIB, proof["max_wal_bytes"])
    need = 5 * idx + reserve + GIB
    require(proof.get("replication_slots") == 0 and proof.get("archive_mode") == "off"
            and proof.get("long_xacts") == 0 and proof.get("index_builds") == 0,
            "CONCURRENT_BUILD_OR_WAL_RETENTION_RISK")
    require(available >= need, "INDEX_DISK_WAL_BUDGET_INSUFFICIENT")
    return {"available": available, "index_estimate": idx, "required": need}


def free_bytes() -> int:
    require(PGDATA.resolve() == PGDATA
            and os.stat(PGDATA).st_dev == os.stat(PGDATA / "pg_wal").st_dev,
            "PGDATA_WAL_MOUNT_UNVERIFIED")
    vfs = os.statvfs(PGDATA)
    return vfs.f_bavail * vfs.f_frsize


def backup() -> dict:
    latest = json.loads(read_bytes(LATEST, root_owned=True))
    stamp = latest.get("created_at_utc", "")
    require(latest.get("state") == "verified"
            and re.fullmatch(r"20\d{6}T\d{6}Z", stamp) is not None,
            "BACKUP_MARKER_UNVERIFIED")
    created = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    require(0 <= (datetime.now(UTC) - created).total_seconds() <= 7200,
            "FRESH_BACKUP_REQUIRED")
    directory = BACKUPS / stamp
    require(latest.get("path") == str(directory) and directory.resolve() == directory,
            "BACKUP_PATH_UNVERIFIED")
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
            and info.st_mode & 0o077 == 0, "BACKUP_DIRECTORY_UNPROTECTED")
    checksums = read_bytes(directory / "SHA256SUMS", root_owned=True).decode("ascii")
    match = re.findall(r"(?m)^([0-9a-f]{64}) [ *]cripta\.pgdump$", checksums)
    require(len(match) == 1, "BACKUP_DUMP_CHECKSUM_MISSING")
    dump = directory / "cripta.pgdump"
    require(dump.resolve() == dump, "BACKUP_DUMP_PATH_UNVERIFIED")
    fd = os.open(dump, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and info.st_mode & 0o077 == 0 and info.st_size > 0,
                "BACKUP_DUMP_UNPROTECTED")
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    require(actual == match[0], "BACKUP_DUMP_HASH_MISMATCH")
    toc = subprocess.run(
        ["/usr/bin/pg_restore", "--list", str(dump)], env=ENV, cwd="/",
        capture_output=True, text=True, check=False, timeout=20,
    )
    require(toc.returncode == 0 and "TABLE runtime account_state_generations " in toc.stdout
            and "TABLE DATA runtime account_state_generations " in toc.stdout,
            "BACKUP_TABLE_TOC_UNVERIFIED")
    return {"stamp": stamp, "dump_bytes": info.st_size, "sha256": actual,
            "target_toc": True, "full_restore_tested": False}


@contextmanager
def release_lock():
    import fcntl

    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and info.st_nlink == 1 and info.st_mode & 0o077 == 0,
                "RELEASE_LOCK_UNTRUSTED")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Blocked("RELEASE_LOCK_BUSY") from exc
        yield
    finally:
        os.close(fd)


def execute(mode: str, up: bytes, down: bytes) -> dict:
    before = probe()
    validate(before, allow_invalid=mode == "--rollback")
    if mode == "--rollback":
        if before["index"] is not None:
            psql(down, readonly=False, timeout=145)
    else:
        headroom = budget(before, free_bytes())
        checkpoint = backup()
        # Recheck the actual state after hashing the backup.
        before = probe()
        validate(before)
        headroom = budget(before, free_bytes())
        if mode == "--check":
            return {"state": "CHECK_ONLY", "db": before, "budget": headroom,
                    "backup": checkpoint, "runtime_behavior_verified": False}
        if before["index"] is None:
            psql(up, readonly=False, timeout=145)
    after = probe()
    validate(after)
    require((after["index"] is None) == (mode == "--rollback"),
            "INDEX_POSTCONDITION_UNVERIFIED")
    return {"state": "ROLLBACK_INDEX_ABSENT" if mode == "--rollback"
            else "INDEX_VALID_READY_LIVE", "db": after,
            "runtime_behavior_verified": False}


def main() -> int:
    try:
        mode = parse_mode(sys.argv[1:])
        verify_installed()
        up, down = source_bytes()
        if mode == "--check":
            report = execute(mode, up, down)
        else:
            with release_lock():
                report = execute(mode, up, down)
        print(json.dumps(report, sort_keys=True))
        return 0
    except (Blocked, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        code = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
        print(json.dumps({"state": "BLOCKED_OR_UNKNOWN", "reason": code,
                          "runtime_behavior_verified": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
