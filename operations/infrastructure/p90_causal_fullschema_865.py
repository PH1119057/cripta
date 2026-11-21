#!/usr/bin/env python3
"""#865 optional C2 real migration-schema parity smoke, disposable PG16 ONLY.

Uses the existing canonical GitHub lifecycle CI migration list and bootstrap.
Distinct from the C2 synthetic 13 tests: this checks an actual 66-migration
PostgreSQL schema and the original private prestart-recovery read query.
Never connects to production PostgreSQL or uses Bybit credentials.
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg

from operations.infrastructure.p90_causal_pg16_testenv import (
    C2Blocked,
    _PG_BIN,
    _ROOT,
    _STAGE,
    _RELEASES,
    _ARCHIVES,
    _admin,
    _check_pg16,
    _pgctl_status,
    _preflight_code,
    _prove_isolated_cluster,
    _start_cluster,
    _test_cluster_dirs,
)

_MIGRATION_PATTERN = re.compile(r"^operations/sql/[A-Za-z0-9_]+\.sql$")
_SCOPE = "C2_FULL_LIFECYCLE_CI_PG16_MIGRATION_SCHEMA"
_SQL = "tests/postgres_trade_lifecycle_bootstrap.sql"
_WORKFLOW = ".github/workflows/trade-lifecycle-postgres.yml"
_TEST_DB = "cripta_integration"
_EXPECTED_MIGRATIONS = 66


def _migration_list(root: Path):
    workflow = root / _WORKFLOW
    bootstrap = root / _SQL
    if not workflow.is_file() or not bootstrap.is_file():
        raise C2Blocked("FULL_SCHEMA_BOOTSTRAP_OR_CI_WORKFLOW_MISSING")
    content = workflow.read_text(encoding="utf-8")
    first = content.find("migrations=(")
    last = content.find("\n          )", first)
    if first < 0 or last <= first:
        raise C2Blocked("FULL_SCHEMA_MIGRATION_LIST_MISSING")
    part = content[first + len("migrations=("):last]
    migrations = [item.strip() for item in part.splitlines() if item.strip()]
    if len(migrations) != _EXPECTED_MIGRATIONS or len(set(migrations)) != len(migrations):
        raise C2Blocked("FULL_SCHEMA_MIGRATION_COUNT_OR_DUPLICATES")
    for relative in migrations:
        if not _MIGRATION_PATTERN.fullmatch(relative):
            raise C2Blocked("FULL_SCHEMA_UNEXPECTED_MIGRATION_TOKEN")
        file = root / relative
        if not file.is_file() or file.is_symlink():
            raise C2Blocked("FULL_SCHEMA_MIGRATION_NOT_IN_CANDIDATE")
    return tuple(migrations)


def _sql_client(root: Path, relative: str):
    source = root / relative
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("PG") and k not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env.update({
        "PGHOST": str(_ROOT / "socket"), "PGPORT": "6546",
        "PGUSER": "postgres", "PGDATABASE": _TEST_DB,
        "PGCONNECT_TIMEOUT": "4",
    })
    p = subprocess.run(
        [str(_PG_BIN / "psql"), "-X", "-v", "ON_ERROR_STOP=1",
         "-d", _TEST_DB, "-f", str(source)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, timeout=35, check=False,
    )
    if p.returncode:
        # Log only the static failing Git path and PostgreSQL exit code,
        # never connection strings or SQL text from external diagnostics.
        raise C2Blocked(
            "FULL_SCHEMA_PSQL_FAILED:" + relative
            + ":rc=" + str(p.returncode)
        )


def _create_db_and_role():
    with _admin("postgres") as c:
        _prove_isolated_cluster(c)
        role = c.execute(
            "SELECT rolname,rolsuper,rolcreatedb,rolcreaterole "
            "FROM pg_roles WHERE rolname='cripta'"
        ).fetchone()
        if role is None:
            c.execute(
                "CREATE ROLE cripta LOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            )
        elif role != ("cripta", False, False, False):
            raise C2Blocked("FULL_SCHEMA_TEST_ROLE_UNSAFE")
        c.execute("DROP DATABASE IF EXISTS cripta_integration WITH (FORCE)")
        c.execute("CREATE DATABASE cripta_integration")


def _bootstrap(root: Path, manifest: dict):
    _sql_client(root, _SQL)
    with _admin(_TEST_DB) as c:
        c.execute(
            """ALTER TABLE runtime.connection_events
                ADD COLUMN at_epoch_ms bigint NOT NULL DEFAULT 0,
                ADD COLUMN channel text NOT NULL DEFAULT '',
                ADD COLUMN event text NOT NULL DEFAULT '',
                ADD COLUMN details_json text NOT NULL DEFAULT '{}'"""
        )
    migrations = _migration_list(root)
    manifest["expected_migrations"] = len(migrations)
    print("C2_FULL_SCHEMA_MIGRATIONS=START count=" + str(len(migrations)), flush=True)
    for n, relative in enumerate(migrations, start=1):
        _sql_client(root, relative)
        manifest["completed_migrations"] = n
        if n % 10 == 0 or n == len(migrations):
            print(
                "C2_FULL_SCHEMA_PROGRESS=" + str(n) + "/" + str(len(migrations)),
                flush=True,
            )


def _check_real_schema(root: Path, manifest: dict):
    with _admin(_TEST_DB) as admin:
        for target in (
            "runtime.position_ownership", "runtime.trade_commands",
            "strategy_exit.exit_observations", "strategy_exit.exit_decisions",
            "strategy_exit.execution_requests", "strategy_exit.execution_dispatches",
            "analytics.native_public_trade_source_sessions",
            "analytics.market_publications", "analytics.public_market_source_seconds",
        ):
            if admin.execute("SELECT to_regclass(%s)", (target,)).fetchone()[0] is None:
                raise C2Blocked("FULL_SCHEMA_REQUIRED_TABLE_MISSING:" + target)
        row = admin.execute(
            """SELECT count(*) FROM pg_class c JOIN pg_namespace n
                   ON n.oid=c.relnamespace
                WHERE n.nspname IN ('runtime','strategy_exit','analytics','identity')
                  AND c.relkind IN ('r','p')"""
        ).fetchone()[0]
        manifest["actual_tables"] = row
        if row < 35:
            raise C2Blocked("FULL_SCHEMA_TABLE_COVERAGE_TOO_SMALL")
        # Original runtime SQL exact string, no copied or normalized fake.
        file = root / "operations/connectivity/private_runtime.py"
        tree = ast.parse(file.read_text(encoding="utf-8"))
        matches = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "resolve_prestart_entry_commands"
        ]
        if len(matches) != 1:
            raise C2Blocked("FULL_SCHEMA_PRIVATE_PRESTART_OWNER_NOT_FOUND")
        statements = [
            node.value for node in ast.walk(matches[0])
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "JOIN strategy_exit.exit_observations o" in node.value
        ]
        if len(statements) != 1:
            raise C2Blocked("FULL_SCHEMA_PRIVATE_PRESTART_SQL_AMBIGUOUS")
        original_sql = statements[0]
        with admin.transaction():
            admin.execute("SET LOCAL ROLE cripta")
            if admin.execute(
                original_sql,
                ("synthetic-entry-never-present", "synthetic-account-never-present"),
            ).fetchone() is not None:
                raise C2Blocked("FULL_SCHEMA_ORIGINAL_PRESTART_NONEMPTY")
        manifest["prestart_actual_code_sql"] = "PASS"

    # Do not assert this is production effective ACL: these grants come from
    # the exact CI migration suite as an independently reproduced schema.
    with psycopg.connect(
        host=str(_ROOT / "socket"), port=6546,
        dbname=_TEST_DB, user="cripta", connect_timeout=3,
    ) as c:
        if c.execute("SELECT current_user,current_database()").fetchone() != (
            "cripta", _TEST_DB
        ):
            raise C2Blocked("FULL_SCHEMA_RUNTIME_ROLE_IDENTITY")
        for target in (
            "runtime.position_ownership", "runtime.trade_commands",
            "strategy_exit.exit_decisions", "analytics.market_publications",
        ):
            if not c.execute(
                "SELECT has_table_privilege(current_user,%s,'SELECT')",
                (target,),
            ).fetchone()[0]:
                raise C2Blocked("FULL_SCHEMA_RUNTIME_READ_GRANT_MISSING")
    manifest["schema_subset_read_smoke"] = "PASS"


def run_full_schema(
    code_root: Path, commit: str, *, stage: bool = False,
    candidate_archive: bool = False,
) -> bool:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:10]
    manifest: dict = {
        "run_id": run_id, "candidate_commit": commit, "state": "UNKNOWN",
        "scope": _SCOPE, "schema_source": _WORKFLOW,
        "stage_worktree": stage, "candidate_archive": candidate_archive,
        "production_database": False, "exchange_mutation": False,
        "started_utc": datetime.now(UTC).isoformat(),
    }
    previous_mask = os.umask(0o077)
    try:
        if os.geteuid() != 107:
            raise C2Blocked("FULL_SCHEMA_UNIX_ACTOR_NOT_CRIPTA")
        _preflight_code(
            code_root, commit, stage=stage, candidate_archive=candidate_archive,
        )
        _test_cluster_dirs()
        _check_pg16()
        with (_ROOT / ".c2.lock").open("a+") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise C2Blocked("FULL_SCHEMA_CONCURRENT_TESTENV") from exc
            _start_cluster()
            try:
                _create_db_and_role()
                _bootstrap(code_root, manifest)
                _check_real_schema(code_root, manifest)
                manifest["state"] = "PASS"
                print(
                    "C2_FULL_SCHEMA_CI_66=PASS tables="
                    + str(manifest["actual_tables"]), flush=True,
                )
            finally:
                try:
                    with _admin("postgres") as c:
                        _prove_isolated_cluster(c)
                        c.execute(
                            "DROP DATABASE IF EXISTS cripta_integration WITH (FORCE)"
                        )
                finally:
                    r = subprocess.run(
                        [str(_PG_BIN / "pg_ctl"), "-D", str(_ROOT / "cluster"),
                         "-m", "fast", "-w", "-t", "25", "stop"],
                        capture_output=True, timeout=35, check=False,
                    )
                    manifest["cluster_stopped"] = (
                        r.returncode == 0
                        and _pgctl_status(_ROOT / "cluster") == 3
                    )
                    if not manifest["cluster_stopped"]:
                        raise C2Blocked("FULL_SCHEMA_CLUSTER_STOP_UNVERIFIED")
    except Exception as exc:
        manifest["state"] = "FAIL"
        manifest["reason"] = (
            str(exc) if isinstance(exc, C2Blocked)
            else "FULL_SCHEMA_UNCLASSIFIED_" + type(exc).__name__
        )
        print("C2_FULL_SCHEMA_CI_66=FAIL:" + manifest["reason"], flush=True)
        return False
    finally:
        manifest["finished_utc"] = datetime.now(UTC).isoformat()
        if (_ROOT / "runs").is_dir():
            target = _ROOT / "runs" / (run_id + ".json")
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True))
            os.replace(tmp, target)
            print("C2_FULL_SCHEMA_MANIFEST=" + str(target), flush=True)
        os.umask(previous_mask)
    return manifest["state"] == "PASS"


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stage-worktree", action="store_true")
    mode.add_argument("--candidate-archive", action="store_true")
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args(argv)
    root = (
        _STAGE if args.stage_worktree
        else (_ARCHIVES if args.candidate_archive else _RELEASES)
        / args.expected_commit
    )
    return 0 if run_full_schema(
        root, args.expected_commit,
        stage=args.stage_worktree,
        candidate_archive=args.candidate_archive,
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
