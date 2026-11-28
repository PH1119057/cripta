"""#865 narrow PG16 access-path regression; not C2 or production acceptance.

Run on robot as cripta with Python 3 and psycopg, no arguments. The only
writes are to a new private temporary cluster under the approved testenvs
parent on the Data SSD. SQL admin is postgres INSIDE that test cluster.
No production DSN, socket, credentials, systemd, or Exchange use.
"""
from __future__ import annotations

import json
import os
import pathlib
import pwd
import shutil
import subprocess
import tempfile

import psycopg

ROOT = pathlib.Path(__file__).resolve().parents[2]
PARENT = pathlib.Path("/data/cripta/research/testenvs")
PG = pathlib.Path("/usr/lib/postgresql/16/bin")
INDEX = "ix_account_state_generations_proven_terminal_account_started"
UP = ROOT / "operations/sql/20261128_p90_account_generation_lookup_index.sql"
DOWN = ROOT / "operations/sql/rollback/20261128_p90_account_generation_lookup_index.sql"
PORT = 55491
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
QUERY = """
SELECT generation_id,state,started_at,completed_at,positions_count,active_orders_count
FROM runtime.account_state_generations
WHERE trading_account_id=%s AND account_connector_id=%s
  AND identity_status='PROVEN' AND state IN ('COMPLETE','FAILED')
ORDER BY started_at DESC,generation_id DESC LIMIT 1
"""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def run(*args: object, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(x) for x in args], env=ENV, check=True, capture_output=True,
        text=True, timeout=timeout,
    )


def plan(conn: psycopg.Connection, params: tuple[str, str]) -> list:
    result = conn.execute("EXPLAIN (FORMAT JSON) " + QUERY, params).fetchone()
    def walk(node: dict) -> list:
        return [(node["Node Type"], node.get("Index Name"))] + [
            item for child in node.get("Plans", []) for item in walk(child)
        ]
    return walk(result[0][0]["Plan"])


def values(conn: psycopg.Connection) -> list:
    result = []
    for params in (("A", "C1"), ("A", "C2"), ("B", "C1"), ("missing", "C1")):
        row = conn.execute(QUERY, params).fetchone()
        result.append(row[:2] if row else None)
    return result


def main() -> None:
    require(
        pwd.getpwuid(os.getuid()).pw_name == "cripta"
        and PARENT.resolve() == PARENT
        and PARENT.stat().st_uid == os.getuid(),
        "ISOLATED_TEST_ACTOR_OR_PATH_UNVERIFIED",
    )
    # Prevent a drifted fixture from validating a different access path.
    normalized = "".join(QUERY.split())
    for name in ("p90l90_real_recovery_account.py", "p90l90_real_flat_account.py"):
        source = ROOT / "src/bybit_workbench/universal_exit" / name
        require(normalized in "".join(source.read_text().split()), "QUERY_DRIFT:" + name)
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="p90_generation_865_", dir=PARENT))
    data, socket = scratch / "cluster", scratch / "socket"
    socket.mkdir(mode=0o700)
    connargs = dict(host=str(socket), port=PORT, connect_timeout=3, autocommit=True)
    started = False

    def apply(path: pathlib.Path, *, expect_pass: bool = True) -> None:
        result = subprocess.run(
            [str(PG / "psql"), "-X", "-v", "ON_ERROR_STOP=1", "-h", str(socket),
             "-p", str(PORT), "-U", "postgres", "-d", "cripta", "-f", str(path)],
            env=ENV, capture_output=True, text=True, timeout=20, check=False,
        )
        require((result.returncode == 0) == expect_pass, "MIGRATION_RESULT:" + path.name)
        if not expect_pass:
            require("P90_GENERATION_INDEX_DEFINITION_OR_STATE_UNVERIFIED" in result.stderr,
                    "UNEXPECTED_REFUSAL")

    try:
        run(PG / "initdb", "-D", data, "-U", "postgres", "--auth-local=trust",
            "--auth-host=reject", "--no-locale", "--encoding=UTF8", "--no-sync")
        run(PG / "pg_ctl", "-D", data, "-l", scratch / "postgres.log", "-o",
            f"-c listen_addresses='' -k {socket} -p {PORT} "
            "-c unix_socket_permissions=0700 -c max_connections=8 "
            "-c shared_buffers=16MB -c statement_timeout=5000",
            "-w", "-t", "10", "start")
        started = True
        with psycopg.connect(**connargs, dbname="postgres", user="postgres") as admin:
            identity = admin.execute(
                "SELECT current_user,current_setting('data_directory'),"
                "current_setting('unix_socket_directories'),current_setting('listen_addresses'),"
                "current_setting('server_version_num')"
            ).fetchone()
            require(identity[:4] == ("postgres", str(data), str(socket), "")
                    and identity[4].startswith("16"), "ISOLATED_PG_IDENTITY_UNVERIFIED")
            admin.execute("CREATE DATABASE cripta")
            admin.execute("CREATE ROLE cripta LOGIN NOSUPERUSER NOCREATEROLE NOCREATEDB")
        with psycopg.connect(**connargs, dbname="cripta", user="postgres") as admin:
            admin.execute("CREATE SCHEMA runtime")
            admin.execute("""CREATE TABLE runtime.account_state_generations (
                generation_id text PRIMARY KEY, account_ref text NOT NULL,
                trading_account_id text, account_connector_id text, identity_status text,
                state text, started_at timestamptz, completed_at timestamptz,
                positions_count integer, active_orders_count integer)""")
            admin.execute("""CREATE INDEX ix_account_state_generations_account_started
                ON runtime.account_state_generations (account_ref, started_at DESC)""")
            admin.execute("GRANT USAGE ON SCHEMA runtime TO cripta")
            admin.execute("GRANT SELECT,INSERT,UPDATE ON runtime.account_state_generations TO cripta")
            admin.execute("""INSERT INTO runtime.account_state_generations
                SELECT 'acctgen-'||lpad(to_hex(i),32,'0'),'BYBIT:UNIFIED',
                       'ta-'||lpad(to_hex(i%3),32,'0'),
                       'ac-'||lpad(to_hex(i%3),32,'0'),'PROVEN',
                       CASE WHEN i%9=0 THEN 'COLLECTING' ELSE 'COMPLETE' END,
                       now()-interval '2 days'+i*interval '1 second',
                       now()-interval '2 days'+i*interval '1 second',0,0
                  FROM generate_series(1,60000) i""")
            fixture = [
                ("a-complete", "A", "C1", "PROVEN", "COMPLETE", 1),
                ("a-failed-a", "A", "C1", "PROVEN", "FAILED", 2),
                ("a-failed-z", "A", "C1", "PROVEN", "FAILED", 2),
                ("a-collecting", "A", "C1", "PROVEN", "COLLECTING", 3),
                ("a-unproven", "A", "C1", "UNPROVEN", "COMPLETE", 4),
                ("a-other", "A", "C2", "PROVEN", "COMPLETE", 5),
                ("b-complete", "B", "C1", "PROVEN", "COMPLETE", 6),
            ]
            with admin.cursor() as cursor:
                cursor.executemany("""INSERT INTO runtime.account_state_generations VALUES
                    (%s,'BYBIT:UNIFIED',%s,%s,%s,%s,
                    timestamp with time zone '2026-11-28 00:00:00+00'+%s*interval '1 second',
                    now(),0,0)""", fixture)
            admin.execute("ANALYZE runtime.account_state_generations")
            with psycopg.connect(**connargs, dbname="cripta", user="cripta") as reader:
                reader.execute("SET default_transaction_read_only=on")
                require(reader.execute("SELECT current_user,current_setting('transaction_read_only')")
                        .fetchone() == ("cripta", "on"), "READER_IDENTITY")
                grants_sql = """SELECT has_table_privilege(current_user,
                    'runtime.account_state_generations',p) FROM
                    unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE']) p"""
                grants = reader.execute(grants_sql).fetchall()
                before, old = plan(reader, ("A", "C1")), values(reader)
                require(any("Seq Scan" in t for t, _ in before), "PRE_FIX_NOT_REPRODUCED")
                require(old == [("a-failed-z", "FAILED"), ("a-other", "COMPLETE"),
                                ("b-complete", "COMPLETE"), None], "FIXTURE_SEMANTICS")
                wal_start = admin.execute("SELECT pg_current_wal_insert_lsn()").fetchone()[0]
                apply(UP)
                after = plan(reader, ("A", "C1"))
                require(any(n == INDEX for _, n in after)
                        and not any("Seq Scan" in t or "Sort" in t for t, _ in after),
                        "SELECTIVE_INDEX_NOT_USED")
                require(values(reader) == old, "SELECTION_SEMANTICS_CHANGED")
                meta = admin.execute("""SELECT indisvalid,indisready,indislive,
                    pg_relation_size(indexrelid),indexrelid FROM pg_index
                    WHERE indexrelid=%s::regclass""", ("runtime."+INDEX,)).fetchone()
                require(meta[:3] == (True,True,True), "INDEX_INVALID")
                wal_bytes = admin.execute("SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),%s)",
                                          (wal_start,)).fetchone()[0]
                apply(UP)
                require(admin.execute("SELECT %s::regclass::oid", ("runtime."+INDEX,))
                        .fetchone()[0] == meta[4], "REPLAY_REBUILT_INDEX")
                require(reader.execute(grants_sql).fetchall() == grants, "GRANTS_CHANGED")
                apply(DOWN)
                apply(DOWN)
                require(plan(reader, ("A", "C1")) == before and values(reader) == old,
                        "ROLLBACK_NOT_RESTORED")
                # Same-name, wrong-definition artifacts must never be silently reused or dropped.
                admin.execute("CREATE INDEX " + INDEX
                              + " ON runtime.account_state_generations (account_ref)")
                apply(UP, expect_pass=False)
                apply(DOWN, expect_pass=False)
                require(admin.execute("SELECT to_regclass(%s) IS NOT NULL",
                                      ("runtime."+INDEX,)).fetchone()[0], "CONFLICTING_INDEX_DROPPED")
                admin.execute("DROP INDEX runtime." + INDEX)
                print(json.dumps({
                    "result": "PASS", "scope": "DISPOSABLE_PG16_ACCESS_PATH_ONLY",
                    "rows": 60007, "before": before, "after": after,
                    "index_bytes": meta[3], "build_wal_bytes": int(wal_bytes),
                    "valid_ready_live": meta[:3], "selection_preserved": True,
                    "grants_preserved": True, "replay_noop": True,
                    "rollback": True, "wrong_definition_fail_closed": True,
                    "production_connection": False,
                }))
    finally:
        if started or (data / "postmaster.pid").exists():
            run(PG / "pg_ctl", "-D", data, "-m", "fast", "-w", "-t", "10", "stop")
        require(not (data / "postmaster.pid").exists(), "ISOLATED_TEST_STOP_UNVERIFIED")
        shutil.rmtree(scratch)
    print("ISOLATED_TEST_CLEANUP_PASS")


if __name__ == "__main__":
    main()
