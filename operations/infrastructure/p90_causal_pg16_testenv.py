#!/usr/bin/env python3
"""#865 independent, persistent PG16 C2 test-environment controller.

Runs only under Unix cripta in a fixed Data-SSD testenv; creates its own
PGDATA/socket, no TCP, no production PostgreSQL, DB credentials or Bybit.
Candidate launcher calls run_c2 before any production service/migration cutover.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg

_SHA = re.compile(r"^[0-9a-f]{40}$")
_ROOT = Path("/data/cripta/research/testenvs/p90_causal_pg16")
_STAGE = Path("/data/cripta/research/worktrees/p90_causal_c2_865_20261119")
_RELEASES = Path("/srv/cripta/runtime/releases")
_ARCHIVES = Path("/data/cripta/research/testenvs/p90_causal_candidate_865")
_PG_BIN = Path("/usr/lib/postgresql/16/bin")
_PORT = 6546
_TEST = "test_p90_causal_pg16_865.py"


class C2Blocked(RuntimeError):
    pass


def _run(argv: list[str], *, timeout: int = 45, check: bool = True):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    result = subprocess.run(
        argv, env=env, capture_output=True, text=True, timeout=timeout,
        check=False,
    )
    if check and result.returncode:
        raise C2Blocked(
            "PG16_CHILD_FAILED:" + Path(argv[0]).name +
            ":rc=" + str(result.returncode)
        )
    return result


def _own_dir(path: Path, *, create: bool = False):
    if path.is_symlink():
        raise C2Blocked("C2_SYMLINK_PATH_DENIED")
    if create and not path.exists():
        path.mkdir(mode=0o700)
    if not path.is_dir():
        raise C2Blocked("C2_TEST_DIR_MISSING")
    stat = path.stat()
    if stat.st_uid != os.geteuid() or (stat.st_mode & 0o077):
        raise C2Blocked("C2_TEST_DIR_ACL_INVALID")


def _preflight_code(
    code_root: Path, commit: str, *, stage: bool, candidate_archive: bool = False,
):
    if not _SHA.fullmatch(commit):
        raise C2Blocked("C2_TARGET_COMMIT_INVALID")
    if code_root.is_symlink() or not code_root.is_dir():
        raise C2Blocked("C2_CODE_ROOT_UNVERIFIED")
    own_file = code_root / "operations/infrastructure/p90_causal_pg16_testenv.py"
    if Path(__file__).resolve() != own_file.resolve():
        raise C2Blocked("C2_RUNNING_CODE_NOT_CANDIDATE")
    test = code_root / "tests" / _TEST
    if not test.is_file() or test.is_symlink():
        raise C2Blocked("C2_CANDIDATE_TEST_MISSING")
    if stage and candidate_archive:
        raise C2Blocked("C2_STAGE_MODE_CONFLICT")
    if candidate_archive:
        if code_root != _ARCHIVES / commit:
            raise C2Blocked("C2_ARCHIVE_CANDIDATE_PATH_INVALID")
        marker = code_root / "INSTALLED_COMMIT"
        if not marker.is_file() or marker.is_symlink():
            raise C2Blocked("C2_ARCHIVE_IDENTITY_MARKER_MISSING")
        if marker.read_text().strip() != commit:
            raise C2Blocked("C2_ARCHIVE_IDENTITY_MISMATCH")
    elif stage:
        if code_root != _STAGE:
            raise C2Blocked("C2_STAGE_PATH_NOT_APPROVED")
        head = _run(["git", "-C", str(code_root), "rev-parse", "HEAD"]).stdout.strip()
        dirty = _run([
            "git", "-C", str(code_root), "status", "--porcelain=v1",
        ]).stdout.strip()
        if head != commit or dirty:
            raise C2Blocked("C2_STAGE_GIT_IDENTITY_OR_DIRTY")
    else:
        if code_root != _RELEASES / commit:
            raise C2Blocked("C2_RELEASE_PATH_INVALID")
        if (code_root / "INSTALLED_COMMIT").read_text().strip() != commit:
            raise C2Blocked("C2_CANDIDATE_INSTALL_IDENTITY_MISMATCH")
        if Path("/srv/cripta/runtime/current").resolve() == code_root:
            raise C2Blocked("C2_RELEASE_IS_ALREADY_CURRENT")


def _test_cluster_dirs():
    research = Path("/data/cripta/research")
    if research.is_symlink() or not research.is_dir():
        raise C2Blocked("C2_RESEARCH_ROOT_INVALID")
    if research.stat().st_uid != os.geteuid() or not os.access(
        research, os.W_OK | os.X_OK
    ):
        raise C2Blocked("C2_EFFECTIVE_ACTOR_ROOT_WRITE_DENIED")
    envs = _ROOT.parent
    _own_dir(envs, create=True)
    _own_dir(_ROOT, create=True)
    for p in (_ROOT / "socket", _ROOT / "logs", _ROOT / "runs"):
        _own_dir(p, create=True)


def _check_pg16():
    for name in ("initdb", "pg_ctl", "postgres"):
        candidate = _PG_BIN / name
        if not candidate.is_file() or candidate.is_symlink():
            raise C2Blocked("C2_PG16_BINARY_MISSING")
    ver = _run([str(_PG_BIN / "postgres"), "--version"])
    if "PostgreSQL) 16." not in ver.stdout:
        raise C2Blocked("C2_NOT_POSTGRESQL_16")


def _pgctl_status(data_dir: Path):
    return _run([
        str(_PG_BIN / "pg_ctl"), "-D", str(data_dir), "status",
    ], timeout=10, check=False).returncode


def _start_cluster():
    data_dir = _ROOT / "cluster"
    if data_dir.is_symlink():
        raise C2Blocked("C2_PGDATA_SYMLINK")
    if not data_dir.exists():
        _run([
            str(_PG_BIN / "initdb"), "-D", str(data_dir),
            "--username=postgres", "--auth-local=trust",
            "--auth-host=reject", "--encoding=UTF8",
            "--locale=C", "--no-instructions",
        ], timeout=35)
    _own_dir(data_dir)
    version = data_dir / "PG_VERSION"
    if not version.is_file() or version.read_text().strip() != "16":
        raise C2Blocked("C2_PGDATA_VERSION_INVALID")
    status = _pgctl_status(data_dir)
    if status == 0:
        raise C2Blocked("C2_TEST_CLUSTER_ALREADY_RUNNING_RECONCILE")
    if status != 3:
        raise C2Blocked("C2_TEST_CLUSTER_STATUS_UNKNOWN")
    opts = (
        "-c listen_addresses='' "
        "-c unix_socket_directories=" + str(_ROOT / "socket") + " "
        "-c unix_socket_permissions=0700 "
        "-c port=" + str(_PORT) + " "
        "-c max_connections=16 -c shared_buffers=16MB"
    )
    _run([
        str(_PG_BIN / "pg_ctl"), "-D", str(data_dir),
        "-l", str(_ROOT / "logs/postgres.log"),
        "-w", "-t", "25", "-o", opts, "start",
    ], timeout=35)
    return data_dir


def _admin(dbname: str):
    return psycopg.connect(
        host=str(_ROOT / "socket"), port=_PORT, dbname=dbname,
        user="postgres", autocommit=True, connect_timeout=3,
    )


def _prove_isolated_cluster(c):
    row = c.execute(
        "SELECT current_setting('data_directory'),"
        " current_setting('unix_socket_directories'),"
        " current_setting('listen_addresses'),"
        " current_setting('port'),"
        " current_setting('server_version_num')::int,"
        " inet_server_addr(),current_user,current_database()"
    ).fetchone()
    expected = (
        str(_ROOT / "cluster"), str(_ROOT / "socket"),
        "", str(_PORT),
    )
    if (
        tuple(row[:4]) != expected or not 160000 <= row[4] < 170000
        or row[5] is not None or row[6] != "postgres"
        or row[7] != "postgres"
    ):
        raise C2Blocked("C2_PHYSICAL_CLUSTER_IDENTITY_MISMATCH")


_DDL = (
    "CREATE SCHEMA runtime",
    "CREATE SCHEMA strategy_exit",
    "CREATE SCHEMA analytics",
    """CREATE TABLE runtime.position_ownership (
        position_id text PRIMARY KEY,trading_account_id text NOT NULL,
        symbol text NOT NULL,state text NOT NULL,
        strategy_id text NOT NULL,strategy_version text NOT NULL,
        identity_status text NOT NULL,
        strategy_execution_binding_id text NOT NULL,
        exchange_position_key text NOT NULL,
        position_idx integer NOT NULL,
        initial_protection_confirmed_at timestamptz,
        initial_protection_evidence jsonb NOT NULL DEFAULT '{}'::jsonb
    )""",
    """CREATE TABLE runtime.trade_commands (
        command_id text PRIMARY KEY,command_type text,state text,
        trading_account_id text,strategy_execution_binding_id text,
        identity_status text,payload_json jsonb NOT NULL DEFAULT '{}'::jsonb
    )""",
    """CREATE TABLE strategy_exit.exit_decisions (
        exit_decision_id text PRIMARY KEY,strategy_position_id text,
        strategy_id text,strategy_version text,
        strategy_config_fingerprint text,exit_plan_fingerprint text
    )""",
    """CREATE TABLE strategy_exit.execution_requests (
        execution_request_id text PRIMARY KEY,exit_decision_id text,
        identity_status text,trading_account_id text,
        strategy_execution_binding_id text
    )""",
    """CREATE TABLE strategy_exit.execution_dispatches (
        dispatch_id text PRIMARY KEY,execution_request_id text,
        state text,command_id text
    )""",
    """CREATE TABLE analytics.native_public_trade_source_sessions (
        source_session_id text PRIMARY KEY,market_source_id text,
        producer_release_commit text,state text,
        subscribed_at timestamptz NOT NULL
    )""",
    """CREATE TABLE analytics.market_publications (
        publication_id bigint PRIMARY KEY,market_source_id text,
        source_session_id text,producer_release_commit text,
        as_of_second timestamptz,publication_status text,
        closed_instrument_refs jsonb NOT NULL,
        closed_instrument_coverage jsonb NOT NULL,
        compact_second_refs jsonb NOT NULL,
        compact_field_coverage jsonb NOT NULL
    )""",
    """CREATE TABLE analytics.public_market_source_seconds (
        source_session_id text,instrument_id text,
        observed_second timestamptz,market_source_id text,
        producer_release_commit text,product_category text,
        external_symbol text,source_time_max timestamptz,
        compact_features jsonb NOT NULL,
        PRIMARY KEY(source_session_id,instrument_id,observed_second)
    )""",
    # These positive source-writer grants match current canonical migrations.
    # Other tables remain intentionally reader-only test fixture grants; NEVER
    # infer production effective privileges from this isolated test cluster.
    "GRANT USAGE ON SCHEMA runtime,strategy_exit,analytics TO cripta",
    "GRANT SELECT ON ALL TABLES IN SCHEMA runtime,strategy_exit,analytics TO cripta",
    "GRANT SELECT,INSERT,UPDATE ON analytics.native_public_trade_source_sessions TO cripta",
    "GRANT SELECT,INSERT ON analytics.market_publications TO cripta",
    "GRANT SELECT,INSERT ON analytics.public_market_source_seconds TO cripta",
    "GRANT SELECT,INSERT ON strategy_exit.exit_decisions,"
    "strategy_exit.execution_requests,"
    "strategy_exit.execution_dispatches TO cripta",
)


def _prepare_test_db():
    with _admin("postgres") as c:
        _prove_isolated_cluster(c)
        role = c.execute(
            "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole"
            " FROM pg_roles WHERE rolname='cripta'"
        ).fetchone()
        if role is None:
            c.execute(
                "CREATE ROLE cripta LOGIN NOSUPERUSER "
                "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            )
        elif role != ("cripta", False, False, False):
            raise C2Blocked("C2_TEST_RUNTIME_ROLE_UNSAFE")
        # Drop is legal ONLY after proven independent PGDATA/socket/no TCP.
        c.execute("DROP DATABASE IF EXISTS cripta_integration WITH (FORCE)")
        c.execute("CREATE DATABASE cripta_integration")
    with _admin("cripta_integration") as c:
        if c.execute("SELECT current_database()").fetchone()[0] != "cripta_integration":
            raise C2Blocked("C2_DB_SCOPE_INVALID")
        for sql in _DDL:
            c.execute(sql)


def run_c2(
    code_root: Path, commit: str, *, stage: bool = False,
    candidate_archive: bool = False,
) -> bool:
    start = datetime.now(UTC)
    run_id = start.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:10]
    old_umask = os.umask(0o077)
    cluster_started = False
    manifest = {
        "run_id": run_id, "candidate_commit": commit, "stage": stage,
        "candidate_archive": candidate_archive,
        "started_at_utc": start.isoformat(), "state": "UNKNOWN",
        "scope": "INDEPENDENT_PG16_SOURCE_PENDING_LOCK_ROLE_MOCK_PEER_IPC",
        "exchange_mutation": False, "production_database": False,
    }
    try:
        if os.geteuid() != 107:
            raise C2Blocked("C2_EXPECTED_UNIX_CRIPTA_UID")
        _preflight_code(
            code_root, commit, stage=stage,
            candidate_archive=candidate_archive,
        )
        _test_cluster_dirs()
        _check_pg16()
        with (_ROOT / ".c2.lock").open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise C2Blocked("C2_TESTENV_CONCURRENT_RUN") from exc
            _start_cluster()
            cluster_started = True
            try:
                _prepare_test_db()
                os.environ["CRIPTA_P90_C2_EXACT_ISOLATED"] = "1"
                sys.dont_write_bytecode = True
                sys.path[:0] = [str(code_root / "tests"), str(code_root), str(code_root / "src")]
                suite = unittest.defaultTestLoader.discover(
                    start_dir=str(code_root / "tests"), pattern=_TEST,
                )
                result = unittest.TextTestRunner(verbosity=2).run(suite)
                manifest["tests"] = result.testsRun
                manifest["failures"] = len(result.failures)
                manifest["errors"] = len(result.errors)
                manifest["skipped"] = len(result.skipped)
                manifest["failed_cases"] = [
                    {"id": case.id(), "traceback_tail": trace[-3500:]}
                    for case, trace in (*result.failures, *result.errors)
                ]
                if not result.wasSuccessful() or result.testsRun < 13 or result.skipped:
                    raise C2Blocked("C2_SQL_TEST_SUITE_FAILED")
                manifest["state"] = "PASS"
                print("C2_PG16_SOURCE_LOCK_ROLE=PASS tests=" + str(result.testsRun), flush=True)
                print("C2_REAL_AF_UNIX_SYNTHETIC_PRIVATE_IPC=PASS", flush=True)
                print("C2_AUTHENTICATED_BYBIT_PRIVATE_IPC=NOT_EXERCISED", flush=True)
                print("C3_SIGNED_BYBIT=NOT_EXERCISED", flush=True)
            finally:
                os.environ.pop("CRIPTA_P90_C2_EXACT_ISOLATED", None)
                if cluster_started:
                    try:
                        with _admin("postgres") as c:
                            _prove_isolated_cluster(c)
                            c.execute(
                                "DROP DATABASE IF EXISTS cripta_integration WITH (FORCE)"
                            )
                    except Exception as exc:
                        manifest["teardown_db_error_type"] = type(exc).__name__
                    _run([
                        str(_PG_BIN / "pg_ctl"),
                        "-D", str(_ROOT / "cluster"),
                        "-m", "fast", "-w", "-t", "25", "stop",
                    ], timeout=35)
                    cluster_started = False
                manifest["cluster_stopped"] = _pgctl_status(
                    _ROOT / "cluster"
                ) == 3
                if not manifest["cluster_stopped"]:
                    raise C2Blocked("C2_CLUSTER_NOT_STOPPED")
                if manifest.get("teardown_db_error_type"):
                    raise C2Blocked("C2_DISPOSABLE_DB_TEARDOWN_FAILED")
    except Exception as exc:
        manifest["state"] = "FAIL"
        manifest["failure_type"] = type(exc).__name__
        manifest["reason"] = (
            str(exc) if isinstance(exc, C2Blocked)
            else "C2_UNCLASSIFIED_EXCEPTION"
        )
        print("C2_PG16_SOURCE_LOCK_ROLE=FAIL reason=" + manifest["reason"], flush=True)
        return False
    finally:
        manifest["finished_at_utc"] = datetime.now(UTC).isoformat()
        if _ROOT.is_dir() and (_ROOT / "runs").is_dir():
            path = _ROOT / "runs" / (run_id + ".json")
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
            os.replace(tmp, path)
            print("C2_AUDIT_MANIFEST=" + str(path), flush=True)
        os.umask(old_umask)
    return manifest["state"] == "PASS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--stage-worktree", action="store_true")
    modes.add_argument("--candidate-archive", action="store_true")
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    code_root = (
        _STAGE if args.stage_worktree
        else (_ARCHIVES if args.candidate_archive else _RELEASES)
        / args.expected_commit
    )
    return 0 if run_c2(
        code_root, args.expected_commit, stage=args.stage_worktree,
        candidate_archive=args.candidate_archive,
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
